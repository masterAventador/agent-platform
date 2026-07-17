"""C12 复审整改：暂停语义、自动暂停不被解除、冲突分类与极端频率。

集中覆盖两轮复审指出的同源根因——`scheduling_dispatch` 对状态写入结果一律不检查，
导致暂停语义在派发路径缺失、自动暂停靠 CAS 巧合才没被解除、冲突分类过宽把真实
数据完整性故障伪装成「另一个副本抢先了」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_platform.infrastructure.database.repositories import scheduling_dispatch
from agent_platform.infrastructure.database.repositories.scheduling import (
    ScheduledTaskExecutionRecord,
    SqlAlchemyScheduledTaskExecutionRepository,
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.infrastructure.database.repositories.scheduling_dispatch import (
    run_scheduler_tick,
)
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.scheduling.entities import (
    ConcurrencyPolicy,
    ExecutionStatus,
    PauseReason,
    ScheduledTask,
    ScheduledTaskExecution,
    SkipReason,
)
from agent_platform.platform.scheduling.schedule import Schedule
from agent_platform.platform.tenants.memberships import TenantRole
from tests.integration.scheduling.conftest import seed_workspace
from tests.integration.scheduling.test_scheduler_tick import (
    CREATED_AT,
    FIRST_TRIGGER,
    JUST_AFTER_TRIGGER,
    add_task,
    load_executions,
    load_runs,
    load_task,
    settle_run,
)

SECOND_TRIGGER = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


async def pause(factory: async_sessionmaker, task: ScheduledTask) -> ScheduledTask:
    """按用户点「暂停」的真实语义落库（enabled=False + next_run_at=None）。"""

    paused = task.pause(now=CREATED_AT)
    async with factory() as session:
        assert await SqlAlchemyScheduledTaskRepository(session).update_with_cas(
            paused, expected_revision=task.revision
        )
        await session.commit()
    return paused


@pytest.mark.asyncio
async def test_pausing_a_task_stops_its_queued_trigger_from_ever_dispatching(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.QUEUE)

    # 11:00 触发 → Run#1 运行中；12:00 触发 → 排队。
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    first_run = (await load_runs(session_factory))[0]
    await run_scheduler_tick(
        session_factory, now=SECOND_TRIGGER + timedelta(seconds=5), batch_limit=50
    )
    deferred = [
        item
        for item in await load_executions(session_factory, task)
        if item.status is ExecutionStatus.DEFERRED
    ]
    assert len(deferred) == 1

    # 用户点暂停，然后上一轮跑完。
    stored = await load_task(session_factory, task)
    await pause(session_factory, stored)
    await settle_run(session_factory, first_run.id, RunStatus.COMPLETED, seed.tenant_id)

    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 30, tzinfo=UTC), batch_limit=50
    )

    # 用户按下了暂停，系统绝不能再给他起新任务。
    assert len(await load_runs(session_factory)) == 1
    executions = await load_executions(session_factory, task)
    settled = [item for item in executions if item.scheduled_for == SECOND_TRIGGER][0]
    assert settled.status is ExecutionStatus.SKIPPED
    assert settled.skip_reason is SkipReason.TASK_PAUSED


@pytest.mark.asyncio
async def test_pausing_a_task_stops_its_pending_retry_from_ever_dispatching(
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
    assert (await load_executions(session_factory, task))[0].status is (
        ExecutionStatus.RETRY_WAITING
    )

    stored = await load_task(session_factory, task)
    await pause(session_factory, stored)

    # 退避到点：暂停中的任务不得因为重试而复活。
    await run_scheduler_tick(
        session_factory, now=settle_at + timedelta(seconds=61), batch_limit=50
    )

    assert len(await load_runs(session_factory)) == 1
    execution = (await load_executions(session_factory, task))[0]
    assert execution.status is ExecutionStatus.SKIPPED
    assert execution.skip_reason is SkipReason.TASK_PAUSED


@pytest.mark.asyncio
async def test_a_paused_tasks_pending_execution_is_settled_not_re_scanned_forever(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.QUEUE)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    first_run = (await load_runs(session_factory))[0]
    await run_scheduler_tick(
        session_factory, now=SECOND_TRIGGER + timedelta(seconds=5), batch_limit=50
    )
    await pause(session_factory, await load_task(session_factory, task))
    await settle_run(session_factory, first_run.id, RunStatus.COMPLETED, seed.tenant_id)

    later = datetime(2026, 7, 17, 12, 30, tzinfo=UTC)
    await run_scheduler_tick(session_factory, now=later, batch_limit=50)
    # 结算成终态后不得再被 pending 扫描捞出，否则每跳都白占派发预算。
    async with session_factory() as session:
        pending = await SqlAlchemyScheduledTaskExecutionRepository(session).list_pending_dispatch(
            now=later, limit=50
        )
    assert pending == []


@pytest.mark.asyncio
async def test_auto_pause_survives_the_same_tick_and_is_not_reverted(
    session_factory: async_sessionmaker,
) -> None:
    # 员工未发布 → 守卫失败 → 自动暂停。同一跳里不得再把任务写回 enabled。
    seed = await seed_workspace(session_factory, published=False)
    task = await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    stored = await load_task(session_factory, task)
    # 直接断言最终状态，不依赖「第二次 CAS 恰好失败」这种巧合。
    assert stored.enabled is False
    assert stored.pause_reason is PauseReason.EMPLOYEE_NOT_RUNNABLE
    assert stored.next_run_at is None


@pytest.mark.asyncio
async def test_an_auto_paused_task_is_not_dispatched_on_later_ticks(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory, published=False)
    task = await add_task(session_factory, seed)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    await run_scheduler_tick(session_factory, now=SECOND_TRIGGER, batch_limit=50)
    await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 18, 0, 0, tzinfo=UTC), batch_limit=50
    )

    assert await load_runs(session_factory) == []
    # 自动暂停后不再每跳产生新的跳过历史（否则历史无界增长）。
    assert len(await load_executions(session_factory, task)) == 1


@pytest.mark.asyncio
async def test_a_conflicting_trigger_point_is_reported_as_already_claimed(
    session_factory: async_sessionmaker,
) -> None:
    """生产路径真正撞上触发点唯一索引时的分支（此前零覆盖）。"""

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    # 另一个副本已经结算了 11:00 这个触发点（终态记录仍占着唯一键）。
    async with session_factory() as session:
        await SqlAlchemyScheduledTaskExecutionRepository(session).add(
            ScheduledTaskExecution.create(
                tenant_id=seed.tenant_id,
                scheduled_task_id=task.id,
                scheduled_for=FIRST_TRIGGER,
                status=ExecutionStatus.SKIPPED,
                skip_reason=SkipReason.CONCURRENCY_SKIPPED,
                now=CREATED_AT,
            )
        )
        await session.commit()

    result = await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    # 触发点冲突是正常竞态：不产生 Run、不计失败、不误报告警。
    assert await load_runs(session_factory) == []
    assert result.dispatched == 0
    assert result.failed == 0
    assert len(await load_executions(session_factory, task)) == 1


@pytest.mark.asyncio
async def test_an_integrity_error_from_run_creation_is_reported_as_a_failure(
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非触发点的数据完整性故障不得被伪装成「另一个副本抢先了」而静默丢弃。"""

    seed = await seed_workspace(session_factory)
    await add_task(session_factory, seed)

    async def exploding_create_employee_run(**_: object) -> None:
        raise IntegrityError("INSERT INTO runs ...", (), Exception("runs_pkey conflict"))

    monkeypatch.setattr(
        scheduling_dispatch, "create_employee_run", exploding_create_employee_run
    )

    result = await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert await load_runs(session_factory) == []
    assert result.dispatched == 0
    assert result.failed == 1


@pytest.mark.asyncio
async def test_a_task_deleted_mid_tick_does_not_count_as_a_scheduling_failure(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)
    async with session_factory() as session:
        await SqlAlchemyScheduledTaskRepository(session).delete(
            tenant_id=task.tenant_id, task_id=task.id
        )
        await session.commit()

    result = await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    assert result.failed == 0
    assert await load_runs(session_factory) == []


@pytest.mark.asyncio
async def test_an_every_minute_cron_dispatches_one_run_per_minute_without_pile_up(
    session_factory: async_sessionmaker,
) -> None:
    """极端频率（每分钟）：默认 SKIP 并发策略下不会堆积成并行 Run。"""

    seed = await seed_workspace(session_factory)
    task = await add_task(
        session_factory,
        seed,
        schedule=Schedule.cron(expression="* * * * *", timezone="UTC"),
    )
    assert (await load_task(session_factory, task)).next_run_at == datetime(
        2026, 7, 17, 10, 1, tzinfo=UTC
    )

    # 连跑 5 分钟，每分钟一跳；上一轮始终未结算。
    now = datetime(2026, 7, 17, 10, 1, tzinfo=UTC)
    for _ in range(5):
        await run_scheduler_tick(session_factory, now=now, batch_limit=50)
        now += timedelta(minutes=1)

    # 只有第一个触发点真跑；其余 4 个因上一轮未结束被跳过，不会滚雪球。
    assert len(await load_runs(session_factory)) == 1
    executions = await load_executions(session_factory, task)
    skipped = [item for item in executions if item.status is ExecutionStatus.SKIPPED]
    assert len(skipped) == 4
    assert {item.skip_reason for item in skipped} == {SkipReason.CONCURRENCY_SKIPPED}


@pytest.mark.asyncio
async def test_an_every_minute_cron_with_allow_produces_one_run_per_trigger(
    session_factory: async_sessionmaker,
) -> None:
    """ALLOW 策略下每分钟真产生一个 Run——成本由 C16 配额治理，C12 不设频率下限。"""

    seed = await seed_workspace(session_factory)
    await add_task(
        session_factory,
        seed,
        schedule=Schedule.cron(expression="* * * * *", timezone="UTC"),
        concurrency_policy=ConcurrencyPolicy.ALLOW,
    )

    now = datetime(2026, 7, 17, 10, 1, tzinfo=UTC)
    for _ in range(3):
        await run_scheduler_tick(session_factory, now=now, batch_limit=50)
        now += timedelta(minutes=1)

    assert len(await load_runs(session_factory)) == 3


@pytest.mark.asyncio
async def test_updating_grace_and_backfill_window_is_persisted_by_cas(
    session_factory: async_sessionmaker,
) -> None:
    """CAS 不得悄悄丢弃字段：写什么就必须存什么。"""

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    from dataclasses import replace

    retuned = replace(
        task,
        misfire_grace_seconds=17,
        misfire_backfill_window_seconds=1234,
        revision=task.revision + 1,
    )
    async with session_factory() as session:
        assert await SqlAlchemyScheduledTaskRepository(session).update_with_cas(
            retuned, expected_revision=task.revision
        )
        await session.commit()

    stored = await load_task(session_factory, task)
    assert stored.misfire_grace_seconds == 17
    assert stored.misfire_backfill_window_seconds == 1234


@pytest.mark.asyncio
async def test_the_scheduler_leaves_no_execution_rows_behind_for_other_tenants(
    session_factory: async_sessionmaker,
) -> None:
    other = await seed_workspace(session_factory, role=TenantRole.OWNER)
    mine = await seed_workspace(session_factory)
    await add_task(session_factory, mine)

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ScheduledTaskExecutionRecord).where(
                    ScheduledTaskExecutionRecord.tenant_id == other.tenant_id
                )
            )
        ).scalars()
    assert list(rows) == []


@pytest.mark.asyncio
async def test_a_guard_reason_that_does_not_pause_advances_to_the_next_trigger(
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未来 C16 配额这类「不暂停、只跳过本次」的守卫原因必须推进 next_run_at。

    锁定 `_claim_one` 的两个分支各自成立，而不是依赖「_advance 的 CAS 恰好失败」
    这种巧合——一旦新增不映射 PauseReason 的 SkipReason，巧合就会碎。
    """

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)

    # 模拟一个不映射 PauseReason 的守卫原因（配额类语义：本次不跑，下次照跑）。
    monkeypatch.setattr(
        scheduling_dispatch,
        "evaluate_dispatch_guards",
        lambda _task, _context: SkipReason.MISFIRE_SKIPPED,
    )

    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)

    stored = await load_task(session_factory, task)
    assert await load_runs(session_factory) == []
    # 任务保持启用，并推进到下一个触发点（不因一次跳过而停摆）。
    assert stored.enabled is True
    assert stored.pause_reason is None
    assert stored.next_run_at == SECOND_TRIGGER
