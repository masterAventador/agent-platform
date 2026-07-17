"""共享 PG 夹具清理的结构性门禁（T9）。

集成测试此前各自维护「手工删表清单」按人肉顺序 delete 一遍。任何人新增一张带
``users`` / ``tenants`` 外键的表，都会在某个不相关的测试文件里炸出 FK 错误，且
报错位置离根因很远（C16 的 ``tenant_model_gateway_policies`` 只是第一个踩中的）。

本门禁锁定共享清理实现的结构性契约：

① 清空 ``Base.metadata`` 注册的所有业务表——**包含没有任何外键的「孤岛表」**
   （``run_dead_letters`` / ``tool_audit_events`` 的 tenant_id/user_id 是裸 uuid
   列、不建约束）。孤岛表既躲得过 CASCADE、也最容易被手工清单漏掉，因此必须
   由门禁直接钉死；
② 未注册到 ``Base.metadata``、但有外键路径通到已登记表的表也必须被连带清空；
③ 绝不清空 ``alembic_version`` 等迁移簿记表，否则后续测试文件的迁移状态被抹掉；
④ 遇到别的连接持锁时必须**快失败并给出可诊断的错误**，而不是无限挂住；
⑤ 目标库带有人工维护的 Demo Seed 时必须拒绝执行，不得把常驻开发栈清空。

需 TEST_DATABASE_URL 才运行；缺失时 skip（不假绿）。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from agent_platform.bootstrap.demo_seed import DEMO_EMAIL
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import ToolAuditRecord
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.dead_letters import (
    RunDeadLetterRecord,
)
from agent_platform.infrastructure.database.repositories.model_gateway import (
    TenantModelGatewayPolicyRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from tests.fixtures.postgres_reset import (
    DatabaseResetLockTimeout,
    DatabaseResetRefused,
    reset_database,
)

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
    try:
        await reset_database(engine)
        yield engine
        await reset_database(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_tenant_and_user(factory: async_sessionmaker) -> tuple[UUID, UUID]:
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


async def _seed_every_shape_of_leftover(factory: async_sessionmaker) -> None:
    """种下三类残留：CASCADE 可达的下游、以及两张没有任何外键的孤岛表。

    孤岛表是这道门禁的要害。只种 tenant/user/policy 的话，「退回手工清单」这种
    变异能全绿溜过去——手工清单靠 ``delete(users)``/``delete(tenants)`` 的
    CASCADE 顺带清掉 42 张表，唯独够不到 ``run_dead_letters`` 和
    ``tool_audit_events``（裸 uuid 列、无 FK 约束）。
    """

    tenant_id, user_id = await _seed_tenant_and_user(factory)
    async with factory() as session:
        # CASCADE 可达：C16 手工清单遗漏的那张，其 updated_by 外键当初挡住了 delete users
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
        # 孤岛表 1：tenant_id 是裸 uuid 列，没有 FK → CASCADE 够不到
        session.add(
            RunDeadLetterRecord(
                id=uuid4(),
                source_stream="t9-stream",
                original_delivery_id=f"t9-{uuid4().hex[:8]}",
                tenant_id=tenant_id,
                attempts=1,
                error_type="T9Probe",
                is_malformed=False,
                raw_fields_summary={},
                failed_at=NOW,
            )
        )
        # 孤岛表 2：tenant_id/user_id 都是裸 uuid 列，没有 FK → CASCADE 够不到
        session.add(
            ToolAuditRecord(
                id=uuid4(),
                event_type="started",
                occurred_at=NOW,
                tenant_id=tenant_id,
                run_id=uuid4(),
                employee_id=uuid4(),
                user_id=user_id,
                tool_id=uuid4(),
                tool_name="t9-probe",
                argument_keys=[],
                argument_sha256="0" * 64,
                argument_size_bytes=0,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_reset_empties_every_table_registered_in_metadata(
    engine: AsyncEngine,
    session_factory: async_sessionmaker,
) -> None:
    """清理必须让 metadata 里每张业务表归零——含无外键的孤岛表。"""

    load_database_models()
    await _seed_every_shape_of_leftover(session_factory)

    # 先证明种子确实落库了，否则「清理后为空」可能只是根本没种进去的假绿。
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ToolAuditRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(RunDeadLetterRecord)) == 1

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
    对这一类必须靠数据库的外键图自动连带，而不是靠人记得改清单。
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


@pytest.mark.asyncio
async def test_reset_fails_fast_when_another_connection_holds_a_lock(
    migrated_postgres_url: str,
    engine: AsyncEngine,
) -> None:
    """TRUNCATE 要 ACCESS EXCLUSIVE：别的连接持锁时必须快失败，不许挂住。

    旧的 DELETE 只取行锁，最坏是 FK 报错、位置清晰；TRUNCATE 一旦无界等待，
    失败模式会退化成「永久挂住、零诊断」——而挂的正是那几个并发测试文件。
    """

    blocker_engine = create_async_engine(migrated_postgres_url)
    try:
        async with blocker_engine.begin() as blocker:
            # 一个开着的事务读一下 users，即持有 ACCESS SHARE，足以挡住 TRUNCATE。
            await blocker.execute(text("SELECT 1 FROM users LIMIT 1"))

            started = perf_counter()
            with pytest.raises(DatabaseResetLockTimeout) as excinfo:
                # wait_for 是兜底：修复前 reset 会一直挂着，这里超时即判红。
                await asyncio.wait_for(reset_database(engine), timeout=30)
            elapsed = perf_counter() - started

        assert elapsed < 20, f"清理未能快失败，耗时 {elapsed:.1f}s"
        message = str(excinfo.value)
        assert "lock" in message.lower()
        assert "TRUNCATE" in message
    finally:
        await blocker_engine.dispose()


@pytest.mark.asyncio
async def test_reset_refuses_to_wipe_a_database_holding_demo_seed(
    engine: AsyncEngine,
    session_factory: async_sessionmaker,
) -> None:
    """护栏：TEST_DATABASE_URL 误指向常驻开发栈时必须拒绝，而不是清空 Demo Seed。

    库名无法用作判据——``agent-platform-dev`` 与 ``test-mvp-profile.sh`` 的隔离
    验收栈都叫 ``agent_platform``、都在 127.0.0.1、端口随机（见
    ``infra/platform/test-mvp-profile.sh:440``）。因此改用语义判据：库里存在
    人工维护的 Demo Seed 账号，就说明这不是可丢弃的测试库。
    """

    async with session_factory() as session:
        session.add(UserRecord(id=uuid4(), email=DEMO_EMAIL, password_hash="x", created_at=NOW))
        await session.commit()

    try:
        with pytest.raises(DatabaseResetRefused) as excinfo:
            await reset_database(engine)

        # 拒绝之后数据必须原封不动——护栏若「先删再报错」等于没有护栏。
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(UserRecord)) == 1
        assert DEMO_EMAIL in str(excinfo.value)
    finally:
        # 清掉 Demo 账号，否则 engine 夹具的收尾 reset 会被自己的护栏挡下。
        async with session_factory() as session:
            await session.execute(delete(UserRecord))
            await session.commit()
