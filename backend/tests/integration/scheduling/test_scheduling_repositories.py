"""C12 定时任务仓储：往返、租户隔离、触发点唯一约束、CAS 与历史保留清理。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.scheduling import (
    SqlAlchemyScheduledTaskExecutionRepository,
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.platform.scheduling.entities import (
    ConcurrencyPolicy,
    ExecutionStatus,
    MisfirePolicy,
    PauseReason,
    ScheduledTask,
    ScheduledTaskExecution,
    SkipReason,
)
from agent_platform.platform.scheduling.schedule import Schedule

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
TENANT_ID = uuid4()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as active_session:
        yield active_session
    await engine.dispose()


def build_task(*, tenant_id=TENANT_ID, created_by=None, **overrides) -> ScheduledTask:
    return ScheduledTask.create(
        tenant_id=tenant_id,
        employee_id=uuid4(),
        created_by=created_by or uuid4(),
        name="每小时巡检",
        schedule=Schedule.cron(expression="0 * * * *", timezone="Asia/Shanghai"),
        input_data={"topic": "巡检"},
        now=NOW,
        **overrides,
    )


@pytest.mark.asyncio
async def test_scheduled_task_round_trips_with_every_schedule_and_policy_field(
    session: AsyncSession,
) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    task = build_task(
        misfire_policy=MisfirePolicy.RUN_ONCE,
        concurrency_policy=ConcurrencyPolicy.QUEUE,
        max_retries=3,
        retry_backoff_seconds=45,
    )

    await repository.add(task)
    await session.flush()

    assert await repository.get(tenant_id=TENANT_ID, task_id=task.id) == task


@pytest.mark.asyncio
async def test_one_shot_scheduled_task_round_trips(session: AsyncSession) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    task = build_task()
    task = ScheduledTask.create(
        tenant_id=TENANT_ID,
        employee_id=task.employee_id,
        created_by=task.created_by,
        name="明早发周报",
        schedule=Schedule.once(
            run_at=datetime(2026, 7, 18, 1, 0, tzinfo=UTC), timezone="Asia/Shanghai"
        ),
        input_data={},
        now=NOW,
    )

    await repository.add(task)
    await session.flush()

    stored = await repository.get(tenant_id=TENANT_ID, task_id=task.id)
    assert stored == task
    assert stored is not None
    assert stored.schedule.run_at == datetime(2026, 7, 18, 1, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_auto_pause_reason_round_trips(session: AsyncSession) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    task = build_task()
    await repository.add(task)
    await session.flush()

    paused = task.auto_pause(reason=PauseReason.EMPLOYEE_NOT_RUNNABLE, now=NOW)
    assert await repository.update_with_cas(paused, expected_revision=task.revision) is True

    stored = await repository.get(tenant_id=TENANT_ID, task_id=task.id)
    assert stored is not None
    assert stored.pause_reason is PauseReason.EMPLOYEE_NOT_RUNNABLE
    assert stored.enabled is False


@pytest.mark.asyncio
async def test_scheduled_task_is_invisible_to_other_tenants(session: AsyncSession) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    task = build_task()
    await repository.add(task)
    await session.flush()

    assert await repository.get(tenant_id=uuid4(), task_id=task.id) is None


@pytest.mark.asyncio
async def test_listing_can_be_restricted_to_the_creator(session: AsyncSession) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    owner = uuid4()
    mine = build_task(created_by=owner)
    theirs = build_task()
    await repository.add(mine)
    await repository.add(theirs)
    await session.flush()

    all_items, all_total = await repository.list(tenant_id=TENANT_ID, limit=50, offset=0)
    own_items, own_total = await repository.list(
        tenant_id=TENANT_ID, created_by=owner, limit=50, offset=0
    )

    assert all_total == 2
    assert {item.id for item in all_items} == {mine.id, theirs.id}
    assert own_total == 1
    assert [item.id for item in own_items] == [mine.id]


@pytest.mark.asyncio
async def test_cas_update_rejects_a_stale_revision(session: AsyncSession) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    task = build_task()
    await repository.add(task)
    await session.flush()

    paused = task.pause(now=NOW)
    assert await repository.update_with_cas(paused, expected_revision=task.revision) is True
    # 第二个写入方拿着已经过期的 revision：必须失败，不能覆盖。
    assert await repository.update_with_cas(paused, expected_revision=task.revision) is False


@pytest.mark.asyncio
async def test_due_task_candidates_exclude_paused_future_and_exhausted_tasks(
    session: AsyncSession,
) -> None:
    repository = SqlAlchemyScheduledTaskRepository(session)
    due = build_task()
    future = build_task()
    paused = build_task()
    await repository.add(due)
    await repository.add(future)
    await repository.add(paused.pause(now=NOW))
    await session.flush()

    candidates = await repository.list_due_task_ids(
        now=datetime(2026, 7, 17, 11, 0, 30, tzinfo=UTC), limit=50
    )

    assert due.id in candidates
    assert future.id in candidates
    assert paused.id not in candidates

    nothing_due = await repository.list_due_task_ids(now=NOW, limit=50)
    assert nothing_due == []


@pytest.mark.asyncio
async def test_deleting_a_task_removes_its_executions(session: AsyncSession) -> None:
    tasks = SqlAlchemyScheduledTaskRepository(session)
    executions = SqlAlchemyScheduledTaskExecutionRepository(session)
    task = build_task()
    await tasks.add(task)
    await executions.add(
        ScheduledTaskExecution.create(
            tenant_id=TENANT_ID,
            scheduled_task_id=task.id,
            scheduled_for=NOW,
            status=ExecutionStatus.SKIPPED,
            skip_reason=SkipReason.MISFIRE_SKIPPED,
            now=NOW,
        )
    )
    await session.flush()

    assert await tasks.delete(tenant_id=TENANT_ID, task_id=task.id) is True
    await session.flush()

    remaining, total = await executions.list_for_task(
        tenant_id=TENANT_ID, scheduled_task_id=task.id, limit=50, offset=0
    )
    assert remaining == []
    assert total == 0


@pytest.mark.asyncio
async def test_the_same_trigger_point_can_only_be_recorded_once(
    session: AsyncSession,
) -> None:
    tasks = SqlAlchemyScheduledTaskRepository(session)
    executions = SqlAlchemyScheduledTaskExecutionRepository(session)
    task = build_task()
    await tasks.add(task)
    await session.flush()

    def execution() -> ScheduledTaskExecution:
        return ScheduledTaskExecution.create(
            tenant_id=TENANT_ID,
            scheduled_task_id=task.id,
            scheduled_for=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
            status=ExecutionStatus.DEFERRED,
            now=NOW,
        )

    await executions.add(execution())
    await session.flush()

    # 同一触发点第二次落库必须被唯一约束拒绝——这是「绝不重复触发」的最终防线。
    with pytest.raises(IntegrityError):
        await executions.add(execution())
        await session.flush()


@pytest.mark.asyncio
async def test_execution_round_trips_and_lists_newest_first(session: AsyncSession) -> None:
    tasks = SqlAlchemyScheduledTaskRepository(session)
    executions = SqlAlchemyScheduledTaskExecutionRepository(session)
    task = build_task()
    await tasks.add(task)
    older = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
        status=ExecutionStatus.DEFERRED,
        now=NOW,
    )
    newer = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        status=ExecutionStatus.DEFERRED,
        now=NOW,
    )
    await executions.add(older)
    await executions.add(newer)
    await session.flush()

    items, total = await executions.list_for_task(
        tenant_id=TENANT_ID, scheduled_task_id=task.id, limit=50, offset=0
    )

    assert total == 2
    assert [item.id for item in items] == [newer.id, older.id]
    assert items[0] == newer


@pytest.mark.asyncio
async def test_active_executions_report_whether_a_task_is_still_running(
    session: AsyncSession,
) -> None:
    tasks = SqlAlchemyScheduledTaskRepository(session)
    executions = SqlAlchemyScheduledTaskExecutionRepository(session)
    task = build_task()
    await tasks.add(task)
    dispatched = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
        status=ExecutionStatus.DEFERRED,
        now=NOW,
    ).dispatched(run_id=uuid4(), now=NOW)
    await executions.add(dispatched)
    await session.flush()

    assert await executions.list_active_for_task(scheduled_task_id=task.id) == [dispatched]

    settled = dispatched.succeeded(now=NOW)
    assert (
        await executions.update_with_cas(settled, expected_revision=dispatched.revision) is True
    )
    await session.flush()

    assert await executions.list_active_for_task(scheduled_task_id=task.id) == []


@pytest.mark.asyncio
async def test_pending_dispatch_covers_deferred_and_due_retries_only(
    session: AsyncSession,
) -> None:
    tasks = SqlAlchemyScheduledTaskRepository(session)
    executions = SqlAlchemyScheduledTaskExecutionRepository(session)
    task = build_task()
    await tasks.add(task)

    deferred = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=datetime(2026, 7, 17, 11, 0, tzinfo=UTC),
        status=ExecutionStatus.DEFERRED,
        now=NOW,
    )
    due_retry = (
        ScheduledTaskExecution.create(
            tenant_id=TENANT_ID,
            scheduled_task_id=task.id,
            scheduled_for=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
            status=ExecutionStatus.DEFERRED,
            now=NOW,
        )
        .dispatched(run_id=uuid4(), now=NOW)
        .awaiting_retry(next_attempt_at=NOW - timedelta(seconds=1), now=NOW)
    )
    future_retry = (
        ScheduledTaskExecution.create(
            tenant_id=TENANT_ID,
            scheduled_task_id=task.id,
            scheduled_for=datetime(2026, 7, 17, 13, 0, tzinfo=UTC),
            status=ExecutionStatus.DEFERRED,
            now=NOW,
        )
        .dispatched(run_id=uuid4(), now=NOW)
        .awaiting_retry(next_attempt_at=NOW + timedelta(hours=1), now=NOW)
    )
    for execution in (deferred, due_retry, future_retry):
        await executions.add(execution)
    await session.flush()

    pending = await executions.list_pending_dispatch(now=NOW, limit=50)

    assert {item.id for item in pending} == {deferred.id, due_retry.id}


@pytest.mark.asyncio
async def test_retention_purge_only_removes_old_terminal_executions(
    session: AsyncSession,
) -> None:
    tasks = SqlAlchemyScheduledTaskRepository(session)
    executions = SqlAlchemyScheduledTaskExecutionRepository(session)
    task = build_task()
    await tasks.add(task)

    old_terminal = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=NOW - timedelta(days=90),
        status=ExecutionStatus.SKIPPED,
        skip_reason=SkipReason.MISFIRE_SKIPPED,
        now=NOW - timedelta(days=90),
    )
    old_active = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=NOW - timedelta(days=91),
        status=ExecutionStatus.DEFERRED,
        now=NOW - timedelta(days=91),
    )
    recent_terminal = ScheduledTaskExecution.create(
        tenant_id=TENANT_ID,
        scheduled_task_id=task.id,
        scheduled_for=NOW - timedelta(days=1),
        status=ExecutionStatus.SKIPPED,
        skip_reason=SkipReason.MISFIRE_SKIPPED,
        now=NOW - timedelta(days=1),
    )
    for execution in (old_terminal, old_active, recent_terminal):
        await executions.add(execution)
    await session.flush()

    purged = await executions.purge_terminal_before(cutoff=NOW - timedelta(days=30), limit=100)
    await session.flush()

    assert purged == 1
    remaining, _ = await executions.list_for_task(
        tenant_id=TENANT_ID, scheduled_task_id=task.id, limit=50, offset=0
    )
    # 活跃执行不因过期被误删（它还没有终态，删掉会丢结算）。
    assert {item.id for item in remaining} == {old_active.id, recent_terminal.id}
