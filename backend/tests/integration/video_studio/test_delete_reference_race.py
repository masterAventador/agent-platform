"""L-2 TOCTOU 门禁：delete_material 与 add_reference 的并发窗口（真实 PostgreSQL）。

需要 TEST_DATABASE_URL；缺失时跳过。SQLite 不执行 FOR UPDATE，
行级锁语义只能在真实 PG 上验证。

不变量：任何交错下都不允许出现「素材已删除但引用仍存在」。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.capabilities.video_studio.media_library import (
    Material,
    MaterialInUseError,
    MaterialKind,
    MaterialNotFoundError,
    MediaLibraryService,
)
from agent_platform.capabilities.video_studio.persistence import (
    SqlAlchemyMediaLibraryRepository,
    VideoMaterialRecord,
    VideoMaterialReferenceRecord,
)
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord

BACKEND_ROOT = Path(__file__).parents[3]


class _UnusedProvider:
    async def issue_upload_credentials(self, **kwargs: Any) -> Any:
        raise AssertionError("race test must not issue credentials")

    async def inspect_uploaded_object(self, **kwargs: Any) -> Any:
        raise AssertionError("race test must not verify objects")


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 并发门禁")

    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


def _service(session: Any) -> MediaLibraryService:
    unused = _UnusedProvider()
    return MediaLibraryService(
        repository=SqlAlchemyMediaLibraryRepository(session),
        credential_issuer=unused,
        object_verifier=unused,
    )


@pytest.mark.asyncio
async def test_concurrent_add_reference_and_delete_cannot_strand_reference(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)
    material_id = uuid4()

    try:
        async with session_factory() as session:
            session.add(
                TenantRecord(
                    id=tenant_id,
                    name="并发门禁企业",
                    slug=f"race-{tenant_id}",
                    created_at=now,
                )
            )
            session.add(
                UserRecord(
                    id=user_id,
                    email=f"race-{user_id}@example.com",
                    password_hash="x",
                    email_verified=False,
                    created_at=now,
                )
            )
            await session.flush()
            material = Material(
                id=material_id,
                tenant_id=tenant_id,
                owner_id=user_id,
                folder_id=None,
                name="race.mp4",
                kind=MaterialKind.VIDEO,
                media_type="video/mp4",
                size_bytes=1000,
                sha256="a" * 64,
                crc64ecma="700",
                storage_key=f"materials/{tenant_id}/{material_id}/race.mp4",
                status="available",
                tags=(),
                upload_expires_at=now - timedelta(minutes=1),
                cleanup_required=False,
                artifact_id=None,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            await SqlAlchemyMediaLibraryRepository(session).add_material(material)
            await session.commit()

        counted = asyncio.Event()
        proceed = asyncio.Event()

        class PausingRepository(SqlAlchemyMediaLibraryRepository):
            async def count_references(self, *, tenant_id: UUID, material_id: UUID) -> int:
                result = await super().count_references(
                    tenant_id=tenant_id, material_id=material_id
                )
                counted.set()
                await proceed.wait()
                return result

        async def run_delete() -> Exception | None:
            async with session_factory() as session:
                unused = _UnusedProvider()
                service = MediaLibraryService(
                    repository=PausingRepository(session),
                    credential_issuer=unused,
                    object_verifier=unused,
                )
                try:
                    await service.delete_material(
                        tenant_id=tenant_id, actor_id=user_id, material_id=material_id
                    )
                    await session.commit()
                    return None
                except MaterialInUseError as error:
                    return error

        async def run_add_reference() -> Exception | None:
            async with session_factory() as session:
                try:
                    await _service(session).add_reference(
                        tenant_id=tenant_id,
                        actor_id=user_id,
                        material_id=material_id,
                        reference_type="timeline_clip",
                        reference_id=uuid4(),
                    )
                    await session.commit()
                    return None
                except MaterialNotFoundError as error:
                    return error

        delete_task = asyncio.create_task(run_delete())
        await asyncio.wait_for(counted.wait(), timeout=10)
        # 删除事务已通过引用计数检查但尚未落地删除；此时并发创建引用。
        reference_task = asyncio.create_task(run_add_reference())
        # 修复前：引用事务立即提交成功；修复后：其行锁被删除事务持有而阻塞。
        await asyncio.sleep(1.0)
        proceed.set()
        delete_error, reference_error = await asyncio.wait_for(
            asyncio.gather(delete_task, reference_task), timeout=15
        )

        async with session_factory() as session:
            stored = (
                await session.execute(
                    select(VideoMaterialRecord).where(VideoMaterialRecord.id == material_id)
                )
            ).scalar_one()
            reference_count = len(
                (
                    await session.execute(
                        select(VideoMaterialReferenceRecord).where(
                            VideoMaterialReferenceRecord.material_id == material_id
                        )
                    )
                ).scalars().all()
            )

        material_deleted = stored.deleted_at is not None
        # 不变量：已删除素材不得仍有引用。
        assert not (material_deleted and reference_count > 0), (
            f"TOCTOU：素材已删除但仍有 {reference_count} 条引用 "
            f"(delete_error={delete_error!r}, reference_error={reference_error!r})"
        )
        # 两个操作必有一个受控失败或后到方看到一致状态。
        assert material_deleted or reference_count > 0
    finally:
        await engine.dispose()
