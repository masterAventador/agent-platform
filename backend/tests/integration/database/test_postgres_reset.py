"""共享 PG 夹具清理的结构性门禁（T9）。

集成测试此前各自维护「手工删表清单」按人肉顺序 delete 一遍。任何人新增一张带
``users`` / ``tenants`` 外键的表，都会在某个不相关的测试文件里炸出 FK 错误，且
报错位置离根因很远（C16 的 ``tenant_model_gateway_policies`` 只是第一个踩中的）。

本门禁锁定共享清理实现的结构性契约：

① 清空 ``Base.metadata`` 注册的所有业务表；
② 未注册到 ``Base.metadata``、但通过外键指向业务表的表也必须被连带清空——
   新增表时不需要任何人记得改清单，否则这个缺陷会以新的伪装再来一次；
③ 绝不清空 ``alembic_version`` 等迁移簿记表，否则后续测试文件的迁移状态被抹掉。

需 TEST_DATABASE_URL 才运行；缺失时 skip（不假绿）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.model_gateway import (
    TenantModelGatewayPolicyRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from tests.fixtures.postgres_reset import reset_database

BACKEND_ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 7, 17, 13, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 夹具清理门禁")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def engine(migrated_postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_postgres_url)
    await reset_database(engine)
    yield engine
    await reset_database(engine)
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_tenant_and_user(factory: async_sessionmaker) -> tuple[uuid4, uuid4]:  # type: ignore[valid-type]
    tenant_id, user_id = uuid4(), uuid4()
    async with factory() as session:
        session.add(
            TenantRecord(
                id=tenant_id, name="清理门禁", slug=f"t9-{tenant_id.hex[:8]}", created_at=NOW
            )
        )
        session.add(
            UserRecord(
                id=user_id,
                email=f"t9-{user_id.hex[:8]}@example.com",
                password_hash="x",
                created_at=NOW,
            )
        )
        await session.commit()
    return tenant_id, user_id


@pytest.mark.asyncio
async def test_reset_empties_every_table_registered_in_metadata(
    engine: AsyncEngine,
    session_factory: async_sessionmaker,
) -> None:
    """清理必须让 metadata 里每张业务表归零，且不被外键顺序挡下。

    种子里特意包含 C16 手工清单遗漏的 ``tenant_model_gateway_policies``：
    它的 ``updated_by`` 外键正是当初挡住 ``delete(UserRecord)`` 的那一个。
    """

    load_database_models()
    tenant_id, user_id = await _seed_tenant_and_user(session_factory)
    async with session_factory() as session:
        session.add(
            TenantModelGatewayPolicyRecord(
                tenant_id=tenant_id,
                enabled=True,
                allowed_aliases=["general-purpose"],
                budget_microusd=1_000_000,
                budget_period="monthly",
                rpm_limit=60,
                tpm_limit=100_000,
                max_parallel_requests=4,
                revision=1,
                status="active",
                created_at=NOW,
                updated_at=NOW,
                updated_by=user_id,
            )
        )
        await session.commit()

    await reset_database(engine)

    async with session_factory() as session:
        non_empty = [
            (table.name, count)
            for table in Base.metadata.sorted_tables
            if (count := await session.scalar(select(func.count()).select_from(table)))
        ]
    assert non_empty == []


@pytest.mark.asyncio
async def test_reset_cascades_into_tables_not_registered_in_metadata(
    engine: AsyncEngine,
    session_factory: async_sessionmaker,
) -> None:
    """变异验证：新增一张带 users 外键、但未登记到 metadata 的表，清理必须自动覆盖它。

    这正是手工清单模式的结构性缺陷所在——清单漏一张表就在别处炸 FK。清理实现
    必须靠数据库的外键图自动连带，而不是靠人记得改清单。
    """

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE t9_unregistered_probe ("
                "  id uuid PRIMARY KEY,"
                "  user_id uuid NOT NULL REFERENCES users(id)"
                ")"
            )
        )
    try:
        _, user_id = await _seed_tenant_and_user(session_factory)
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO t9_unregistered_probe (id, user_id) VALUES (:i, :u)"),
                {"i": uuid4(), "u": user_id},
            )

        await reset_database(engine)

        async with engine.connect() as connection:
            probe_rows = await connection.scalar(text("SELECT count(*) FROM t9_unregistered_probe"))
            user_rows = await connection.scalar(text("SELECT count(*) FROM users"))
        assert probe_rows == 0
        assert user_rows == 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS t9_unregistered_probe"))


@pytest.mark.asyncio
async def test_reset_preserves_the_alembic_version_bookkeeping(engine: AsyncEngine) -> None:
    """清理绝不能抹掉迁移版本表，否则后续测试文件的迁移状态被清零。"""

    async with engine.connect() as connection:
        before = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert before is not None

    await reset_database(engine)

    async with engine.connect() as connection:
        after = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert after == before
