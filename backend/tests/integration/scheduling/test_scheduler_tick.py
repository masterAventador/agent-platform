"""C12 调度主链：触发产生正常 Run/Command、幂等、重启恢复、并发/错过策略、守卫与重试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_platform.infrastructure.database.repositories.audit import AuditEventRecord
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunRecord,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.scheduling import (
    SqlAlchemyScheduledTaskExecutionRepository,
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.infrastructure.database.repositories.scheduling_dispatch import (
    run_scheduler_tick,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
)
from agent_platform.platform.runs.commands import RunCommandAction
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.scheduling.entities import (
    ConcurrencyPolicy,
    ExecutionStatus,
    MisfirePolicy,
    PauseReason,
    ScheduledTask,
    SkipReason,
)
from agent_platform.platform.scheduling.schedule import Schedule
from tests.integration.scheduling.conftest import (
    SchedulingSeed,
    employee_definition,
    seed_workspace,
)

CREATED_AT = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
FIRST_TRIGGER = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
JUST_AFTER_TRIGGER = FIRST_TRIGGER + timedelta(seconds=5)


async def add_task(
    factory: async_sessionmaker,
    seed: SchedulingSeed,
    *,
    schedule: Schedule | None = None,
    **overrides,
) -> ScheduledTask:
    task = ScheduledTask.create(
        tenant_id=seed.tenant_id,
        employee_id=seed.employee_id,
        created_by=seed.user_id,
        name="每小时巡检",
        schedule=schedule or Schedule.cron(expression="0 * * * *", timezone="UTC"),
        input_data={"topic": "巡检"},
        now=CREATED_AT,
        **overrides,
    )
    async with factory() as session:
        await SqlAlchemyScheduledTaskRepository(session).add(task)
        await session.commit()
    return task


async def load_task(factory: async_sessionmaker, task: ScheduledTask) -> ScheduledTask:
    async with factory() as session:
        stored = await SqlAlchemyScheduledTaskRepository(session).get(
            tenant_id=task.tenant_id, task_id=task.id
        )
    assert stored is not None
    return stored


async def load_executions(factory: async_sessionmaker, task: ScheduledTask) -> list:
    async with factory() as session:
        items, _ = await SqlAlchemyScheduledTaskExecutionRepository(session).list_for_task(
            tenant_id=task.tenant_id, scheduled_task_id=task.id, limit=50, offset=0
        )
    return items


async def load_runs(factory: async_sessionmaker) -> list[RunRecord]:
    async with factory() as session:
        return list((await session.execute(select(RunRecord))).scalars())


async def settle_run(
    factory: async_sessionmaker, run_id: UUID, status: RunStatus, tenant_id: UUID
) -> None:
    """把 Run 推到终态，模拟 Worker 真实执行完成。"""

    async with factory() as session:
        runs = SqlAlchemyRunRepository(session)
        run = await runs.get(tenant_id=tenant_id, run_id=run_id)
        assert run is not None
        running = run.transition_to(RunStatus.RUNNING)
        await runs.update(running)
        await runs.update(running.transition_to(status))
        await session.commit()


@pytest.mark.asyncio
async def test_a_due_task_produces_a_normal_run_with_its_start_command(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    result = await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert result.dispatched == 1
    runs = await load_runs(session_factory)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.QUEUED.value
    assert runs[0].input_data == {"topic": "巡检"}
    assert runs[0].created_by == seed.user_id
    assert runs[0].employee_version == seed.published_version

    async with session_factory() as session:
        commands = list((await session.execute(select(RunCommandRecord))).scalars())
    assert [command.action for command in commands] == [RunCommandAction.START.value]

    executions = await load_executions(session_factory, task)
    assert len(executions) == 1
    assert executions[0].status is ExecutionStatus.DISPATCHED
    assert executions[0].scheduled_for == FIRST_TRIGGER
    assert executions[0].run_id == runs[0].id
    assert executions[0].attempts == 1

    stored = await load_task(session_factory, task)
    assert stored.next_run_at == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    assert stored.last_run_at is not None


@pytest.mark.asyncio
async def test_the_same_trigger_point_never_produces_a_second_run(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.ALLOW)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    # 同一时刻再跑一跳：next_run_at 已推进，11:00 这个触发点不能再产生 Run。
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert len(await load_runs(session_factory)) == 1
    assert len(await load_executions(session_factory, task)) == 1


@pytest.mark.asyncio
async def test_restarting_the_scheduler_resumes_from_the_persisted_next_run_at(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.ALLOW)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    # 进程重启：调度器没有任何内存状态，只能靠库里的 next_run_at 继续。
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC), batch_limit=50
    )

    runs = await load_runs(session_factory)
    executions = await load_executions(session_factory, task)
    assert len(runs) == 2
    assert {execution.scheduled_for for execution in executions} == {
        FIRST_TRIGGER,
        datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    }


@pytest.mark.asyncio
async def test_concurrency_skip_leaves_a_visible_skipped_execution(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.SKIP)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    # 上一轮的 Run 还没结束，下一个触发点到了。
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC), batch_limit=50
    )

    assert len(await load_runs(session_factory)) == 1
    executions = await load_executions(session_factory, task)
    skipped = [item for item in executions if item.status is ExecutionStatus.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].skip_reason is SkipReason.CONCURRENCY_SKIPPED
    assert skipped[0].scheduled_for == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_concurrency_allow_starts_a_parallel_run(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.ALLOW)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC), batch_limit=50
    )

    assert len(await load_runs(session_factory)) == 2


@pytest.mark.asyncio
async def test_concurrency_queue_defers_the_trigger_until_the_active_run_settles(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.QUEUE)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    first_run = (await load_runs(session_factory))[0]

    # 第二个触发点到达时上一轮仍在跑：排队而不是丢弃。
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC), batch_limit=50
    )
    executions = await load_executions(session_factory, task)
    deferred = [item for item in executions if item.status is ExecutionStatus.DEFERRED]
    assert len(deferred) == 1
    assert len(await load_runs(session_factory)) == 1

    # 上一轮结束后，排队的触发点才真正派发。
    await settle_run(session_factory, first_run.id, RunStatus.COMPLETED, seed.tenant_id)
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 30, tzinfo=UTC), batch_limit=50
    )

    assert len(await load_runs(session_factory)) == 2
    executions = await load_executions(session_factory, task)
    assert [item for item in executions if item.status is ExecutionStatus.DEFERRED] == []


@pytest.mark.asyncio
async def test_queue_collapses_further_triggers_while_one_is_already_waiting(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.QUEUE)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 0, 5, tzinfo=UTC), batch_limit=50
    )
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 13, 0, 5, tzinfo=UTC), batch_limit=50
    )

    executions = await load_executions(session_factory, task)
    # 队列深度恒为 1：不能无界堆积待跑触发点。
    assert len([item for item in executions if item.status is ExecutionStatus.DEFERRED]) == 1
    collapsed = [item for item in executions if item.skip_reason is SkipReason.QUEUE_COLLAPSED]
    assert len(collapsed) == 1


@pytest.mark.asyncio
async def test_misfire_skip_records_the_skip_and_moves_on(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, misfire_policy=MisfirePolicy.SKIP)

    # 停机三小时后恢复。
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 13, 30, tzinfo=UTC), batch_limit=50
    )

    assert await load_runs(session_factory) == []
    executions = await load_executions(session_factory, task)
    assert len(executions) == 1
    assert executions[0].status is ExecutionStatus.SKIPPED
    assert executions[0].skip_reason is SkipReason.MISFIRE_SKIPPED

    stored = await load_task(session_factory, task)
    assert stored.next_run_at == datetime(2026, 7, 17, 14, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_misfire_run_once_backfills_exactly_one_run(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, misfire_policy=MisfirePolicy.RUN_ONCE)

    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 13, 30, tzinfo=UTC), batch_limit=50
    )

    assert len(await load_runs(session_factory)) == 1
    stored = await load_task(session_factory, task)
    assert stored.next_run_at == datetime(2026, 7, 17, 14, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_an_unpublished_employee_pauses_the_task_instead_of_running_it(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory, published=False)
    task = await add_task(session_factory, seed)

    result = await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert result.dispatched == 0
    assert await load_runs(session_factory) == []
    executions = await load_executions(session_factory, task)
    assert executions[0].status is ExecutionStatus.SKIPPED
    assert executions[0].skip_reason is SkipReason.EMPLOYEE_NOT_RUNNABLE

    stored = await load_task(session_factory, task)
    assert stored.enabled is False
    assert stored.pause_reason is PauseReason.EMPLOYEE_NOT_RUNNABLE


@pytest.mark.asyncio
async def test_disabling_scheduling_on_the_published_version_pauses_the_task(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(
        session_factory, definition=employee_definition(scheduled_tasks=False)
    )
    task = await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert await load_runs(session_factory) == []
    executions = await load_executions(session_factory, task)
    assert executions[0].skip_reason is SkipReason.SCHEDULED_TASKS_DISABLED
    stored = await load_task(session_factory, task)
    assert stored.pause_reason is PauseReason.SCHEDULED_TASKS_DISABLED


@pytest.mark.asyncio
async def test_revoking_the_creator_membership_pauses_the_task(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    # 创建者被移出企业：调度必须 fail-closed，不能继续代表他跑。
    async with session_factory() as session:
        await session.execute(
            delete(TenantMembershipRecord).where(
                TenantMembershipRecord.user_id == seed.user_id
            )
        )
        await session.commit()

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert await load_runs(session_factory) == []
    executions = await load_executions(session_factory, task)
    assert executions[0].skip_reason is SkipReason.CREATOR_PERMISSION_REVOKED
    stored = await load_task(session_factory, task)
    assert stored.pause_reason is PauseReason.CREATOR_PERMISSION_REVOKED


@pytest.mark.asyncio
async def test_an_input_that_no_longer_matches_the_published_schema_pauses_the_task(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(
        session_factory,
        definition=employee_definition(
            input_schema={
                "type": "object",
                "properties": {"region": {"type": "string"}},
                "required": ["region"],
                "additionalProperties": False,
            }
        ),
    )
    task = await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert await load_runs(session_factory) == []
    executions = await load_executions(session_factory, task)
    assert executions[0].skip_reason is SkipReason.INPUT_SCHEMA_INCOMPATIBLE
    stored = await load_task(session_factory, task)
    assert stored.pause_reason is PauseReason.INPUT_SCHEMA_INCOMPATIBLE


@pytest.mark.asyncio
async def test_guard_failures_are_audited(session_factory: async_sessionmaker) -> None:
    seed = await seed_workspace(session_factory, published=False)
    await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    async with session_factory() as session:
        actions = [
            record.action
            for record in (await session.execute(select(AuditEventRecord))).scalars()
        ]
    assert "scheduled_task.auto_paused" in actions


@pytest.mark.asyncio
async def test_a_completed_run_settles_its_execution_as_succeeded(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await settle_run(session_factory, run.id, RunStatus.COMPLETED, seed.tenant_id)

    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 11, 30, tzinfo=UTC), batch_limit=50
    )

    executions = await load_executions(session_factory, task)
    assert executions[0].status is ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_cancelled_run_settles_its_execution_as_cancelled(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await settle_run(session_factory, run.id, RunStatus.CANCELLED, seed.tenant_id)

    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 11, 30, tzinfo=UTC), batch_limit=50
    )

    executions = await load_executions(session_factory, task)
    assert executions[0].status is ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_failed_run_without_retries_settles_as_failed(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, max_retries=0)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await settle_run(session_factory, run.id, RunStatus.FAILED, seed.tenant_id)

    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 11, 30, tzinfo=UTC), batch_limit=50
    )

    executions = await load_executions(session_factory, task)
    assert executions[0].status is ExecutionStatus.FAILED
    assert len(await load_runs(session_factory)) == 1


@pytest.mark.asyncio
async def test_a_failed_run_with_retries_is_retried_after_the_backoff(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(
        session_factory,
        seed,
        max_retries=1,
        retry_backoff_seconds=60,
        concurrency_policy=ConcurrencyPolicy.ALLOW,
    )

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await settle_run(session_factory, run.id, RunStatus.FAILED, seed.tenant_id)

    settle_at = datetime(2026, 7, 17, 11, 10, tzinfo=UTC)
    await run_scheduler_tick(session_factory, now=settle_at, batch_limit=50)
    executions = await load_executions(session_factory, task)
    assert executions[0].status is ExecutionStatus.RETRY_WAITING
    assert executions[0].next_attempt_at == settle_at + timedelta(seconds=60)
    assert len(await load_runs(session_factory)) == 1

    # 退避未到不重试。
    await run_scheduler_tick(
        session_factory, now=settle_at + timedelta(seconds=30), batch_limit=50
    )
    assert len(await load_runs(session_factory)) == 1

    # 退避到点后重试，产生第二个 Run。
    await run_scheduler_tick(
        session_factory, now=settle_at + timedelta(seconds=61), batch_limit=50
    )
    executions = await load_executions(session_factory, task)
    retried = [item for item in executions if item.scheduled_for == FIRST_TRIGGER][0]
    assert retried.status is ExecutionStatus.DISPATCHED
    assert retried.attempts == 2
    assert len(await load_runs(session_factory)) == 2


@pytest.mark.asyncio
async def test_retries_stop_once_the_budget_is_used_up(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(
        session_factory,
        seed,
        max_retries=1,
        retry_backoff_seconds=1,
        concurrency_policy=ConcurrencyPolicy.ALLOW,
        schedule=Schedule.once(run_at=FIRST_TRIGGER, timezone="UTC"),
    )

    now = JUST_AFTER_TRIGGER
    for _ in range(6):
        runs = await load_runs(session_factory)
        for run in runs:
            if run.status == RunStatus.QUEUED.value:
                await settle_run(session_factory, run.id, RunStatus.FAILED, seed.tenant_id)
        await run_scheduler_tick(session_factory, now=now, batch_limit=50)
        now += timedelta(seconds=30)

    executions = await load_executions(session_factory, task)
    assert executions[0].status is ExecutionStatus.FAILED
    assert executions[0].attempts == 2
    # 1 次初始 + 1 次重试，用尽后不再无限重试。
    assert len(await load_runs(session_factory)) == 2


@pytest.mark.asyncio
async def test_a_one_shot_task_fires_once_and_becomes_exhausted(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(
        session_factory, seed, schedule=Schedule.once(run_at=FIRST_TRIGGER, timezone="UTC")
    )

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 18, 0, 0, tzinfo=UTC), batch_limit=50
    )

    assert len(await load_runs(session_factory)) == 1
    stored = await load_task(session_factory, task)
    assert stored.next_run_at is None
    assert stored.is_exhausted is True


@pytest.mark.asyncio
async def test_a_paused_task_is_never_dispatched(session_factory: async_sessionmaker) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)
    async with session_factory() as session:
        repository = SqlAlchemyScheduledTaskRepository(session)
        await repository.update_with_cas(
            task.pause(now=CREATED_AT), expected_revision=task.revision
        )
        await session.commit()

    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 18, 0, 0, tzinfo=UTC), batch_limit=50
    )

    assert await load_runs(session_factory) == []
    assert await load_executions(session_factory, task) == []


@pytest.mark.asyncio
async def test_one_failing_task_does_not_stop_the_others_in_the_same_tick(
    session_factory: async_sessionmaker,
) -> None:
    broken_seed = await seed_workspace(session_factory, published=False)
    healthy_seed = await seed_workspace(session_factory)
    await add_task(session_factory, broken_seed)
    await add_task(session_factory, healthy_seed)

    result = await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert result.dispatched == 1
    assert len(await load_runs(session_factory)) == 1
