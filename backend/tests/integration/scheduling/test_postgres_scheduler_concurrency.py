"""真实 PostgreSQL 下的调度并发门禁（C12）。

SQLite 的 `SELECT ... FOR UPDATE SKIP LOCKED` 是 no-op、也没有真实 MVCC，
单元/内存层的「多副本并发」不算数。本门禁用真实独立 asyncpg 连接制造真并发，验证：
① 两个副本同时对同一到期任务跑调度，只产生一个 Run 和一条执行记录；
② 同一触发点并发插入被唯一索引挡下（重复触发的最终防线）；
③ next_run_at 的 CAS 只有一方生效，任务不会被并发写回旧值；
④ 进程重启（丢弃全部内存状态）后按库里的 next_run_at 继续，不丢不重。

需 TEST_DATABASE_URL 才运行；缺失时 skip（不假绿）。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.audit import (
    AuditChainStateRecord,
    AuditEventRecord,
)
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunRecord,
)
from agent_platform.infrastructure.database.repositories.scheduling import (
    ScheduledTaskExecutionRecord,
    ScheduledTaskRecord,
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.infrastructure.database.repositories.scheduling_dispatch import (
    run_scheduler_tick,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeStatus,
    EmployeeVersion,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.scheduling.entities import (
    ConcurrencyPolicy,
    ExecutionStatus,
    ScheduledTask,
    ScheduledTaskExecution,
)
from agent_platform.platform.scheduling.schedule import Schedule
from agent_platform.platform.tenants.memberships import TenantRole

BACKEND_ROOT = Path(__file__).parents[3]

CREATED_AT = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
FIRST_TRIGGER = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
JUST_AFTER_TRIGGER = FIRST_TRIGGER + timedelta(seconds=5)


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 调度并发门禁")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def session_factory(migrated_postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(migrated_postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        for record in (
            ScheduledTaskExecutionRecord,
            ScheduledTaskRecord,
            RunCommandRecord,
            RunRecord,
            AuditEventRecord,
            AuditChainStateRecord,
            EmployeeVersionRecord,
            EmployeeRecord,
            TenantMembershipRecord,
            UserRecord,
            TenantRecord,
        ):
            await session.execute(delete(record))
        await session.commit()
    await engine.dispose()


async def seed_task(
    factory: async_sessionmaker, *, concurrency_policy: ConcurrencyPolicy
) -> ScheduledTask:
    tenant_id, user_id, employee_id = uuid4(), uuid4(), uuid4()
    draft = EmployeeDraft(
        name="巡检员",
        avatar_url=None,
        role_description="定时巡检",
        visibility=EmployeeVisibility.TENANT,
        runtime_type=RuntimeType.AUTONOMOUS,
        system_prompt="你是巡检员",
        model_settings={"kind": "gateway_alias", "alias": "general-purpose"},
        input_schema={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        capabilities={"conversation": True, "scheduled_tasks": True, "file_upload": False},
        skill_ids=[],
        tool_ids=[],
        knowledge_base_ids=[],
        approval_policy={},
        release_strategy={"mode": "all"},
    )
    async with factory() as session:
        # 真实 PostgreSQL 强制外键：逐层 flush，保证父行先于子行落库。
        session.add(
            TenantRecord(
                id=tenant_id,
                name="演示企业",
                slug=f"tenant-{tenant_id.hex[:8]}",
                created_at=CREATED_AT,
            )
        )
        await session.flush()
        session.add(
            UserRecord(
                id=user_id,
                email=f"user-{user_id.hex[:8]}@example.com",
                password_hash="x",
                email_verified=True,
                created_at=CREATED_AT,
            )
        )
        await session.flush()
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role=TenantRole.MEMBER.value,
                created_at=CREATED_AT,
            )
        )
        await session.flush()
        employee = Employee.create(tenant_id=tenant_id, created_by=user_id, draft=draft)
        employee = replace(
            employee, id=employee_id, status=EmployeeStatus.PUBLISHED, published_version=1
        )
        await SqlAlchemyEmployeeRepository(session).add(employee)
        await SqlAlchemyEmployeeVersionRepository(session).add(
            EmployeeVersion(
                id=uuid4(),
                employee_id=employee_id,
                tenant_id=tenant_id,
                version=1,
                definition=draft.snapshot(),
                published_by=user_id,
                published_at=CREATED_AT,
            )
        )
        task = ScheduledTask.create(
            tenant_id=tenant_id,
            employee_id=employee_id,
            created_by=user_id,
            name="每小时巡检",
            schedule=Schedule.cron(expression="0 * * * *", timezone="UTC"),
            input_data={"topic": "巡检"},
            now=CREATED_AT,
            concurrency_policy=concurrency_policy,
        )
        await SqlAlchemyScheduledTaskRepository(session).add(task)
        await session.commit()
    return task


async def count(factory: async_sessionmaker, record: type) -> int:
    async with factory() as session:
        total = await session.execute(select(func.count()).select_from(record))
        return int(total.scalar_one())


@pytest.mark.asyncio
async def test_two_replicas_racing_on_one_due_task_create_exactly_one_run(
    session_factory: async_sessionmaker,
) -> None:
    task = await seed_task(session_factory, concurrency_policy=ConcurrencyPolicy.ALLOW)

    # 两个副本用各自独立的引擎/连接池，制造真并发（不是同一连接上的伪并发）。
    replica_a = create_async_engine(os.environ["TEST_DATABASE_URL"])
    replica_b = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        results = await asyncio.gather(
            run_scheduler_tick(
                async_sessionmaker(replica_a, expire_on_commit=False),
                now=JUST_AFTER_TRIGGER,
                batch_limit=50,
            ),
            run_scheduler_tick(
                async_sessionmaker(replica_b, expire_on_commit=False),
                now=JUST_AFTER_TRIGGER,
                batch_limit=50,
            ),
        )
    finally:
        await replica_a.dispose()
        await replica_b.dispose()

    assert await count(session_factory, RunRecord) == 1
    assert await count(session_factory, RunCommandRecord) == 1
    assert await count(session_factory, ScheduledTaskExecutionRecord) == 1
    # 只有一个副本认领成功；另一个被行锁 SKIP LOCKED 或唯一索引挡下。
    assert sum(result.dispatched for result in results) == 1

    async with session_factory() as session:
        stored = await SqlAlchemyScheduledTaskRepository(session).get(
            tenant_id=task.tenant_id, task_id=task.id
        )
    assert stored is not None
    assert stored.next_run_at == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    assert stored.revision == task.revision + 1


@pytest.mark.asyncio
async def test_the_same_trigger_point_cannot_be_inserted_twice_concurrently(
    session_factory: async_sessionmaker,
) -> None:
    task = await seed_task(session_factory, concurrency_policy=ConcurrencyPolicy.ALLOW)

    def execution() -> ScheduledTaskExecution:
        return ScheduledTaskExecution.create(
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            scheduled_for=FIRST_TRIGGER,
            status=ExecutionStatus.DEFERRED,
            now=JUST_AFTER_TRIGGER,
        )

    async def insert(candidate: ScheduledTaskExecution) -> bool:
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add(
                    ScheduledTaskExecutionRecord(
                        id=candidate.id,
                        tenant_id=candidate.tenant_id,
                        scheduled_task_id=candidate.scheduled_task_id,
                        scheduled_for=candidate.scheduled_for,
                        status=candidate.status.value,
                        attempts=candidate.attempts,
                        revision=candidate.revision,
                        created_at=candidate.created_at,
                        updated_at=candidate.updated_at,
                    )
                )
                try:
                    await session.commit()
                    return True
                except IntegrityError:
                    await session.rollback()
                    return False
        finally:
            await engine.dispose()

    outcomes = await asyncio.gather(insert(execution()), insert(execution()))

    assert sorted(outcomes) == [False, True]
    assert await count(session_factory, ScheduledTaskExecutionRecord) == 1


@pytest.mark.asyncio
async def test_restart_resumes_from_the_persisted_next_run_at_without_duplicates(
    session_factory: async_sessionmaker,
) -> None:
    task = await seed_task(session_factory, concurrency_policy=ConcurrencyPolicy.ALLOW)

    # 第一跳：11:00 触发。随后「进程重启」——换全新引擎，内存状态全丢。
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    assert await count(session_factory, RunRecord) == 1

    restarted = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        factory = async_sessionmaker(restarted, expire_on_commit=False)
        # 重启后按同一时刻重跑：不能把 11:00 再触发一次。
        await run_scheduler_tick(factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
        assert await count(session_factory, RunRecord) == 1
        # 到了 12:00 才继续触发下一个点，不丢触发。
        await run_scheduler_tick(
            factory, now=datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC), batch_limit=50
        )
    finally:
        await restarted.dispose()

    assert await count(session_factory, RunRecord) == 2
    async with session_factory() as session:
        points = (
            await session.execute(select(ScheduledTaskExecutionRecord.scheduled_for))
        ).scalars()
        scheduled_points = {value.replace(tzinfo=UTC) for value in points}
    assert scheduled_points == {FIRST_TRIGGER, datetime(2026, 7, 17, 12, 0, tzinfo=UTC)}
    del task


@pytest.mark.asyncio
async def test_concurrent_replicas_respect_the_skip_concurrency_policy(
    session_factory: async_sessionmaker,
) -> None:
    await seed_task(session_factory, concurrency_policy=ConcurrencyPolicy.SKIP)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    replica_a = create_async_engine(os.environ["TEST_DATABASE_URL"])
    replica_b = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        later = datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC)
        await asyncio.gather(
            run_scheduler_tick(
                async_sessionmaker(replica_a, expire_on_commit=False),
                now=later,
                batch_limit=50,
            ),
            run_scheduler_tick(
                async_sessionmaker(replica_b, expire_on_commit=False),
                now=later,
                batch_limit=50,
            ),
        )
    finally:
        await replica_a.dispose()
        await replica_b.dispose()

    # 上一轮仍在跑：第二个触发点只留下一条 skipped 历史，不产生第二个 Run。
    assert await count(session_factory, RunRecord) == 1
    assert await count(session_factory, ScheduledTaskExecutionRecord) == 2
