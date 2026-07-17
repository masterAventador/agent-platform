"""素材库维护清扫（M-2）：过期草稿标记 + cleanup_required 对象回收。

误删矩阵：只允许两类动作——
1) 超过 upload_expires_at 的 pending_upload 草稿 → upload_failed + cleanup_required；
2) cleanup_required=True 的素材 → 调对象清理器删对象后清除标记。
available 素材、未过期草稿一律不得触碰；清理失败保留标记下轮重试（无重复删除副作用）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.capabilities.video_studio.maintenance import (
    sweep_media_library_once,
)
from agent_platform.capabilities.video_studio.media_library import (
    Material,
    MaterialKind,
)
from agent_platform.capabilities.video_studio.persistence import (
    SqlAlchemyMediaLibraryRepository,
)
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models


class RecordingCleaner:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fail_keys: set[str] = set()

    async def delete_object(self, *, tenant_id: UUID, object_key: str) -> None:
        if object_key in self.fail_keys:
            raise OSError("temporary object storage failure")
        self.deleted.append(object_key)


def _material(
    *,
    tenant_id: UUID,
    name: str,
    status: str,
    upload_expires_at: datetime,
    cleanup_required: bool,
    now: datetime,
) -> Material:
    material_id = uuid4()
    return Material(
        id=material_id,
        tenant_id=tenant_id,
        owner_id=uuid4(),
        folder_id=None,
        name=name,
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="a" * 64,
        storage_key=f"materials/{tenant_id}/{material_id}/{name}",
        status=status,
        tags=(),
        upload_expires_at=upload_expires_at,
        cleanup_required=cleanup_required,
        artifact_id=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    materials: list[Material],
) -> None:
    async with session_factory() as session:
        repository = SqlAlchemyMediaLibraryRepository(session)
        for material in materials:
            await repository.add_material(material)
        await session.commit()


async def _status_of(
    session_factory: async_sessionmaker[AsyncSession],
    material: Material,
) -> tuple[str, bool]:
    async with session_factory() as session:
        repository = SqlAlchemyMediaLibraryRepository(session)
        stored = await repository.get_material(
            tenant_id=material.tenant_id, material_id=material.id
        )
        assert stored is not None
        return stored.status, stored.cleanup_required


@pytest.mark.asyncio
async def test_sweep_expires_overdue_drafts_and_cleans_marked_objects(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    tenant_a = uuid4()
    tenant_b = uuid4()
    expired = _material(
        tenant_id=tenant_a,
        name="expired.mp4",
        status="pending_upload",
        upload_expires_at=now - timedelta(minutes=1),
        cleanup_required=False,
        now=now - timedelta(hours=1),
    )
    active_draft = _material(
        tenant_id=tenant_a,
        name="active.mp4",
        status="pending_upload",
        upload_expires_at=now + timedelta(minutes=10),
        cleanup_required=False,
        now=now,
    )
    available = _material(
        tenant_id=tenant_b,
        name="available.mp4",
        status="available",
        upload_expires_at=now - timedelta(hours=2),
        cleanup_required=False,
        now=now - timedelta(hours=3),
    )
    failed_marked = _material(
        tenant_id=tenant_b,
        name="failed.mp4",
        status="upload_failed",
        upload_expires_at=now - timedelta(hours=2),
        cleanup_required=True,
        now=now - timedelta(hours=3),
    )
    await _seed(session_factory, [expired, active_draft, available, failed_marked])

    cleaner = RecordingCleaner()
    report = await sweep_media_library_once(
        session_factory=session_factory,
        object_cleaner=cleaner,
        batch_limit=100,
        clock=lambda: now,
    )

    assert report.expired_drafts == 1
    assert report.cleaned_objects == 2  # 过期草稿 + 既有 cleanup_required
    assert report.failed_cleanups == 0
    assert set(cleaner.deleted) == {expired.storage_key, failed_marked.storage_key}

    assert await _status_of(session_factory, expired) == ("upload_failed", False)
    assert await _status_of(session_factory, active_draft) == ("pending_upload", False)
    assert await _status_of(session_factory, available) == ("available", False)
    assert await _status_of(session_factory, failed_marked) == ("upload_failed", False)

    # 幂等：再次清扫无新动作，不重复删除。
    second = await sweep_media_library_once(
        session_factory=session_factory,
        object_cleaner=cleaner,
        batch_limit=100,
        clock=lambda: now,
    )
    assert (second.expired_drafts, second.cleaned_objects) == (0, 0)
    assert len(cleaner.deleted) == 2


@pytest.mark.asyncio
async def test_sweep_keeps_marker_on_cleaner_failure_and_isolates_per_material(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    tenant_id = uuid4()
    failing = _material(
        tenant_id=tenant_id,
        name="failing.mp4",
        status="upload_failed",
        upload_expires_at=now - timedelta(hours=1),
        cleanup_required=True,
        now=now - timedelta(hours=2),
    )
    succeeding = _material(
        tenant_id=uuid4(),
        name="ok.mp4",
        status="upload_failed",
        upload_expires_at=now - timedelta(hours=1),
        cleanup_required=True,
        now=now - timedelta(hours=2),
    )
    await _seed(session_factory, [failing, succeeding])

    cleaner = RecordingCleaner()
    cleaner.fail_keys.add(failing.storage_key)
    report = await sweep_media_library_once(
        session_factory=session_factory,
        object_cleaner=cleaner,
        batch_limit=100,
        clock=lambda: now,
    )

    # 单个素材清理失败不影响其他素材，也不抛出异常。
    assert report.cleaned_objects == 1
    assert report.failed_cleanups == 1
    assert await _status_of(session_factory, failing) == ("upload_failed", True)
    assert await _status_of(session_factory, succeeding) == ("upload_failed", False)

    # 故障恢复后下一轮重试成功。
    cleaner.fail_keys.clear()
    retry = await sweep_media_library_once(
        session_factory=session_factory,
        object_cleaner=cleaner,
        batch_limit=100,
        clock=lambda: now,
    )
    assert retry.cleaned_objects == 1
    assert await _status_of(session_factory, failing) == ("upload_failed", False)


@pytest.mark.asyncio
async def test_sweep_without_cleaner_expires_drafts_but_keeps_cleanup_markers(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """清理器未配置（无真实 COS 凭据）时仍要止血过期草稿，标记留待配置后回收。"""

    now = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    expired = _material(
        tenant_id=uuid4(),
        name="expired.mp4",
        status="pending_upload",
        upload_expires_at=now - timedelta(minutes=5),
        cleanup_required=False,
        now=now - timedelta(hours=1),
    )
    await _seed(session_factory, [expired])

    report = await sweep_media_library_once(
        session_factory=session_factory,
        object_cleaner=None,
        batch_limit=100,
        clock=lambda: now,
    )

    assert report.expired_drafts == 1
    assert report.cleaned_objects == 0
    assert await _status_of(session_factory, expired) == ("upload_failed", True)
