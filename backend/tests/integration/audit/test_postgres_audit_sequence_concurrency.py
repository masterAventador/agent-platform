"""真实 PostgreSQL 下的每租户审计序列并发唯一性验证。

SQLite 的 ``with_for_update`` 是 no-op，无法证明并发写入不会产生序列冲突；
本文件按仓库既有 PG 门禁模式，缺少 ``TEST_DATABASE_URL`` 时条件跳过。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.audit import (
    AuditEventRecord,
    SqlAlchemyAuditEventRepository,
    emit_audit_event,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
)
from agent_platform.platform.tenants.entities import Tenant

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 审计序列并发测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_postgres_concurrent_audit_writes_keep_tenant_sequence_unique_and_contiguous(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = Tenant.create(name="Audit concurrency", slug=f"audit-seq-{uuid4().hex}")
    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        await session.commit()

    writer_count = 12

    async def write_event(index: int) -> None:
        async with session_factory() as session:
            await emit_audit_event(
                session,
                tenant_id=tenant.id,
                actor_user_id=None,
                action=f"concurrency.writer_{index}",
                resource_type="test",
            )
            await session.commit()

    results = await asyncio.gather(
        *(write_event(index) for index in range(writer_count)),
        return_exceptions=True,
    )

    errors = [result for result in results if isinstance(result, BaseException)]
    assert errors == [], f"并发审计写入不应泄漏异常: {errors!r}"

    async with session_factory() as session:
        sequences = list(
            (
                await session.execute(
                    select(AuditEventRecord.sequence)
                    .where(AuditEventRecord.tenant_id == tenant.id)
                    .order_by(AuditEventRecord.sequence)
                )
            ).scalars()
        )
        verification = await SqlAlchemyAuditEventRepository(session).verify_integrity(
            tenant_id=tenant.id
        )

    assert sequences == list(range(1, writer_count + 1))
    assert verification.valid is True
    assert verification.checked_events == writer_count
    await engine.dispose()
