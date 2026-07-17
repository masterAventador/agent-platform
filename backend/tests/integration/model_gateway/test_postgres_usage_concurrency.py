"""真实 PostgreSQL 下的模型用量记录并发写门禁（C16 阶段二）。

用量捕获在多个 Worker 副本上并发写记录。本门禁用真实独立 asyncpg session 制造真并发，
验证：并发写各自独立行、不撞唯一键、不相互阻塞，全部落库。缺 TEST_DATABASE_URL 时 skip。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.model_usage import (
    ModelUsageRow,
    SessionModelUsageRecorder,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.model_gateway.usage import ModelCallOutcome, ModelUsageRecord
from tests.fixtures.postgres_reset import reset_database

BACKEND_ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 用量并发门禁")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def session_factory(migrated_postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(migrated_postgres_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await reset_database(engine)
        yield factory
        await reset_database(engine)
    finally:
        await engine.dispose()


async def _seed_tenant(factory: async_sessionmaker) -> UUID:
    tenant_id = uuid4()
    async with factory() as session:
        session.add(
            TenantRecord(
                id=tenant_id,
                name="用量并发",
                slug=f"usage-{tenant_id.hex}",
                created_at=NOW,
            )
        )
        await session.commit()
    return tenant_id


def _record(tenant_id: UUID) -> ModelUsageRecord:
    return ModelUsageRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        run_id=uuid4(),
        employee_id=uuid4(),
        model_alias="general-purpose",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=42,
        outcome=ModelCallOutcome.SUCCESS,
        error_type=None,
        cost_nanousd=1_500,
        cost_source="platform_pricing_table",
        recorded_at=NOW,
    )


@pytest.mark.asyncio
async def test_concurrent_writes_all_persist_without_collision(
    session_factory: async_sessionmaker,
) -> None:
    tenant_id = await _seed_tenant(session_factory)
    # 两个独立 recorder（模拟两个 Worker 副本）各自并发写 25 条。
    recorder_a = SessionModelUsageRecorder(session_factory)
    recorder_b = SessionModelUsageRecorder(session_factory)

    async def burst(recorder: SessionModelUsageRecorder, n: int) -> None:
        await asyncio.gather(*(recorder.record(_record(tenant_id)) for _ in range(n)))

    await asyncio.gather(burst(recorder_a, 25), burst(recorder_b, 25))

    async with session_factory() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(ModelUsageRow)
                .where(ModelUsageRow.tenant_id == tenant_id)
            )
        ).scalar_one()
    assert total == 50  # 全部落库，无丢失、无唯一键碰撞
