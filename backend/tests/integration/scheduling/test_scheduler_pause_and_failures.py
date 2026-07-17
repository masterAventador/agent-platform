"""C12 复审整改：暂停语义、自动暂停不被解除、冲突分类与极端频率。

集中覆盖两轮复审指出的同源根因——`scheduling_dispatch` 对状态写入结果一律不检查，
导致暂停语义在派发路径缺失、自动暂停靠 CAS 巧合才没被解除、冲突分类过宽把真实
数据完整性故障伪装成「另一个副本抢先了」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_platform.infrastructure.database.repositories import scheduling_dispatch
from agent_platform.infrastructure.database.repositories.audit import AuditEventRecord
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunRepository,
)
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


@pytest.mark.asyncio
async def test_a_stale_pending_snapshot_on_a_paused_task_is_not_a_scheduling_failure(
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """良性竞态不得被误报成调度失败（否则 A-3 想修的告警失真又从这里回来）。

    副本 A 扫到 DEFERRED → 副本 B 抢先派发（→DISPATCHED）→ 用户暂停 → 副本 A 拿锁
    重读到 enabled=False + DISPATCHED。若暂停分支排在状态守卫之前，就会对 DISPATCHED
    调 skipped() 抛非法转换，被宽 except 接住计入 failed 并触发告警。
    """

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.QUEUE)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    first_run = (await load_runs(session_factory))[0]
    await run_scheduler_tick(
        session_factory, now=SECOND_TRIGGER + timedelta(seconds=5), batch_limit=50
    )
    deferred = [
        item
        for item in await load_executions(session_factory, task)
        if item.status is ExecutionStatus.DEFERRED
    ][0]

    # 副本 B 抢先把它派发掉。
    async with session_factory() as session:
        assert await SqlAlchemyScheduledTaskExecutionRepository(session).update_with_cas(
            deferred.dispatched(run_id=first_run.id, now=SECOND_TRIGGER),
            expected_revision=deferred.revision,
        )
        await session.commit()
    # 用户随后暂停任务。
    await pause(session_factory, await load_task(session_factory, task))

    # 副本 A 手里仍是那份陈旧的 DEFERRED 快照。
    async def stale_scan(_self, *, now: object, limit: object) -> list:
        del now, limit
        return [deferred]

    monkeypatch.setattr(
        SqlAlchemyScheduledTaskExecutionRepository, "list_pending_dispatch", stale_scan
    )

    result = await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 30, tzinfo=UTC), batch_limit=50
    )

    assert result.failed == 0
    assert result.failures == []
    # 已被别人推进的执行不该被本副本改写。
    async with session_factory() as session:
        stored = await SqlAlchemyScheduledTaskExecutionRepository(session).get(
            tenant_id=seed.tenant_id, execution_id=deferred.id
        )
    assert stored is not None
    assert stored.status is ExecutionStatus.DISPATCHED


@pytest.mark.asyncio
async def test_a_dispatched_execution_whose_run_never_terminates_times_out(
    session_factory: async_sessionmaker,
) -> None:
    """Run 永久停在 WAITING_FOR_INPUT 时，执行必须超时结算，否则任务永久静默停摆。

    孤儿恢复扫描（`recover_incomplete_runs`）对 WAITING_FOR_INPUT 是「恢复运行时」
    而不是「终结」，所以它兜不住这条；WAITING_FOR_APPROVAL 另有 C13 审批超时兜底。
    """

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, max_retries=2)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        stored_run = await runs.get(tenant_id=seed.tenant_id, run_id=run.id)
        assert stored_run is not None
        await runs.update(
            stored_run.transition_to(RunStatus.RUNNING).transition_to(
                RunStatus.WAITING_FOR_INPUT
            )
        )
        await session.commit()

    # 超时之前：不得擅自结算一个还活着的 Run。
    await run_scheduler_tick(
        session_factory, now=JUST_AFTER_TRIGGER + timedelta(hours=1), batch_limit=50
    )
    before = [
        item
        for item in await load_executions(session_factory, task)
        if item.scheduled_for == FIRST_TRIGGER
    ][0]
    assert before.status is ExecutionStatus.DISPATCHED

    # 超时之后：结算为失败并放行后续触发点。
    await run_scheduler_tick(
        session_factory, now=JUST_AFTER_TRIGGER + timedelta(days=2), batch_limit=50
    )

    executions = await load_executions(session_factory, task)
    timed_out = [item for item in executions if item.scheduled_for == FIRST_TRIGGER][0]
    assert timed_out.status is ExecutionStatus.FAILED
    assert timed_out.error_message is not None
    assert "timed_out" in timed_out.error_message
    # 超时不得触发重试：原 Run 可能还活着，重试会绕过 SKIP 并发策略再起一个。
    assert timed_out.attempts == 1
    assert len(await load_runs(session_factory)) >= 1


@pytest.mark.asyncio
async def test_a_timed_out_execution_unblocks_the_task_for_later_triggers(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.SKIP)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        stored_run = await runs.get(tenant_id=seed.tenant_id, run_id=run.id)
        assert stored_run is not None
        await runs.update(
            stored_run.transition_to(RunStatus.RUNNING).transition_to(
                RunStatus.WAITING_FOR_INPUT
            )
        )
        await session.commit()

    # 卡住两天：这一跳先超时结算，并按 misfire=skip 把 next_run_at 推到未来。
    await run_scheduler_tick(
        session_factory, now=FIRST_TRIGGER + timedelta(days=2), batch_limit=50
    )
    async with session_factory() as session:
        active = await SqlAlchemyScheduledTaskExecutionRepository(
            session
        ).list_active_for_task(scheduled_task_id=task.id)
    # 并发闸门已放开：任务不再被那条永不终态的执行堵死。
    assert active == []

    # 下一个触发点准时到达时能正常派发（此前 SKIP 策略下会被永久挡住）。
    resumed = await load_task(session_factory, task)
    assert resumed.next_run_at is not None
    await run_scheduler_tick(
        session_factory,
        now=resumed.next_run_at + timedelta(seconds=5),
        batch_limit=50,
    )

    assert len(await load_runs(session_factory)) == 2


async def _park_run(
    session_factory: async_sessionmaker,
    *,
    tenant_id: UUID,
    run_id: UUID,
    status: RunStatus,
) -> None:
    """把 Run 推到指定的非终态，模拟真实运行中的各种停留。"""

    if status is RunStatus.QUEUED:
        return
    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        run = await runs.get(tenant_id=tenant_id, run_id=run_id)
        assert run is not None
        running = run.transition_to(RunStatus.RUNNING)
        await runs.update(running)
        if status is not RunStatus.RUNNING:
            await runs.update(running.transition_to(status))
        await session.commit()


@pytest.mark.parametrize(
    "parked_status",
    [RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.WAITING_FOR_APPROVAL],
)
@pytest.mark.asyncio
async def test_a_run_that_someone_else_terminates_is_never_killed_by_the_timeout(
    session_factory: async_sessionmaker,
    parked_status: RunStatus,
) -> None:
    """超时只针对「无人终结」的状态，不得误杀健康长跑 Run。

    - `running` 的孤儿由 Worker `recover_incomplete_runs` 判失败；
    - `queued` 的命令进死信后由死信结算驱动 run 失败；
    - `waiting_for_approval` 由 C13 审批超时驱动 reject。
    三者都另有终结者，再加一层超时是重复且有害的。
    """

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await _park_run(
        session_factory, tenant_id=seed.tenant_id, run_id=run.id, status=parked_status
    )

    # 远超默认 24h 超时后仍不得结算。
    await run_scheduler_tick(
        session_factory, now=JUST_AFTER_TRIGGER + timedelta(hours=26), batch_limit=50
    )

    execution = [
        item
        for item in await load_executions(session_factory, task)
        if item.scheduled_for == FIRST_TRIGGER
    ][0]
    assert execution.status is ExecutionStatus.DISPATCHED
    assert execution.error_message is None


@pytest.mark.asyncio
async def test_a_healthy_long_run_still_settles_as_succeeded_after_the_timeout_window(
    session_factory: async_sessionmaker,
) -> None:
    """跑了很久但最终成功的 Run，执行历史必须是 succeeded——不能留下与事实相反的失败。

    误杀之后该执行已离开 `list_dispatched` 集合，Run 真正完成时永远不会被回填。
    """

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await _park_run(
        session_factory, tenant_id=seed.tenant_id, run_id=run.id, status=RunStatus.RUNNING
    )

    # 跑了 26 小时……
    await run_scheduler_tick(
        session_factory, now=JUST_AFTER_TRIGGER + timedelta(hours=26), batch_limit=50
    )
    # ……然后成功了。
    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        stored = await runs.get(tenant_id=seed.tenant_id, run_id=run.id)
        assert stored is not None
        await runs.update(stored.transition_to(RunStatus.COMPLETED))
        await session.commit()
    await run_scheduler_tick(
        session_factory, now=JUST_AFTER_TRIGGER + timedelta(hours=27), batch_limit=50
    )

    execution = [
        item
        for item in await load_executions(session_factory, task)
        if item.scheduled_for == FIRST_TRIGGER
    ][0]
    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.error_message is None


@pytest.mark.asyncio
async def test_a_timeout_settlement_is_audited(
    session_factory: async_sessionmaker,
) -> None:
    seed = await seed_workspace(session_factory)
    await add_task(session_factory, seed)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    run = (await load_runs(session_factory))[0]
    await _park_run(
        session_factory,
        tenant_id=seed.tenant_id,
        run_id=run.id,
        status=RunStatus.WAITING_FOR_INPUT,
    )

    await run_scheduler_tick(
        session_factory, now=JUST_AFTER_TRIGGER + timedelta(days=2), batch_limit=50
    )

    async with session_factory() as session:
        actions = [
            record.action
            for record in (await session.execute(select(AuditEventRecord))).scalars()
        ]
    # 把执行改成 failed 并解除并发闸门的状态变更必须留痕（与派发/自动暂停一致）。
    assert "scheduled_task.execution_timed_out" in actions


@pytest.mark.asyncio
async def test_benign_pending_races_do_not_inflate_the_skipped_metric(
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """良性竞态（执行已被别的副本推进）不得混进业务跳过计数，否则指标失真。"""

    seed = await seed_workspace(session_factory)
    task = await add_task(session_factory, seed, concurrency_policy=ConcurrencyPolicy.QUEUE)
    await run_scheduler_tick(session_factory, now=JUST_AFTER_TRIGGER, batch_limit=50)
    first_run = (await load_runs(session_factory))[0]
    await run_scheduler_tick(
        session_factory, now=SECOND_TRIGGER + timedelta(seconds=5), batch_limit=50
    )
    deferred = [
        item
        for item in await load_executions(session_factory, task)
        if item.status is ExecutionStatus.DEFERRED
    ][0]
    async with session_factory() as session:
        assert await SqlAlchemyScheduledTaskExecutionRepository(session).update_with_cas(
            deferred.dispatched(run_id=first_run.id, now=SECOND_TRIGGER),
            expected_revision=deferred.revision,
        )
        await session.commit()

    async def stale_scan(_self, *, now: object, limit: object) -> list:
        del now, limit
        return [deferred]

    monkeypatch.setattr(
        SqlAlchemyScheduledTaskExecutionRepository, "list_pending_dispatch", stale_scan
    )

    result = await run_scheduler_tick(
        session_factory, now=datetime(2026, 7, 17, 12, 30, tzinfo=UTC), batch_limit=50
    )

    assert result.failed == 0
    # 什么都没发生就不该记成"跳过了一次业务触发"。
    assert result.skipped == 0
