"""C12 调度主链的一跳（tick）。

每跳三个阶段，各自按条目独立事务，单条失败只计数不拖垮整跳（与既有审批超时清扫、
审计保留清扫的后台任务语义一致）：

1. 结算：已派发执行对应的 Run 到终态后落成功/失败/取消，失败按预算转入重试等待；
2. 派发待跑：排队中的触发点与退避到点的重试；
3. 认领到期任务：按 misfire/并发策略决定跑还是跳过，并推进 next_run_at。

多副本安全由两道真实原语保证：
- `FOR UPDATE SKIP LOCKED` 认领任务行，同一任务同一跳只被一个副本处理；
- (scheduled_task_id, scheduled_for) 唯一索引是最终防线；插入执行记录在创建 Run
  之前，冲突即整事务回滚，绝不会留下重复 Run。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.run_dispatch import (
    create_employee_run,
)
from agent_platform.infrastructure.database.repositories.runs import SqlAlchemyRunRepository
from agent_platform.infrastructure.database.repositories.scheduling import (
    SqlAlchemyScheduledTaskExecutionRepository,
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyWorkspaceRepository,
)
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.scheduling.entities import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    ConcurrencyPolicy,
    ExecutionStatus,
    ScheduledTask,
    ScheduledTaskExecution,
    SkipReason,
    TriggerOutcome,
    pause_reason_for_guard,
    plan_trigger,
)
from agent_platform.platform.scheduling.guards import (
    DispatchContext,
    evaluate_dispatch_guards,
)

logger = logging.getLogger(__name__)

# 只有「无人终结」的状态才需要调度侧超时兜底。其余非终态各自另有终结者，
# 再加一层超时会误杀健康长跑 Run，且它真正完成时已不在 `list_dispatched` 集合里、
# 永远不会被回填，用户历史里会留下与事实相反的失败记录：
# - running              -> Worker `recover_incomplete_runs` 判孤儿失败；
# - queued               -> 命令重投失败后进死信，死信结算驱动 run FAILED。
#                           **该论证条件于队列消息未丢失**：若 Redis 整体丢数据
#                           （无持久化重启），命令已离开 pending()、DLQ 也无记录，
#                           run 会永久停在 queued。那是基础设施级故障，修复归属
#                           队列层（QUEUED 对账器）而非调度器，不在 C12 范围内；
#                           见 roadmap C12「已知局限」。
# - waiting_for_approval -> C13 审批超时清扫（`sweep_approval_expiry`，已在
#                           `api/app.py` 的 lifespan 生产装配）驱动 reject。
_UNTERMINATED_RUN_STATUSES = frozenset({RunStatus.WAITING_FOR_INPUT})

_TERMINAL_RUN_SETTLEMENTS = {
    RunStatus.COMPLETED: "succeeded",
    RunStatus.FAILED: "failed",
    RunStatus.CANCELLED: "cancelled",
}


@dataclass
class SchedulerTickResult:
    dispatched: int = 0
    skipped: int = 0
    settled: int = 0
    failed: int = 0
    purged_executions: int = 0
    failures: list[str] = field(default_factory=list)


def _repositories(
    session: AsyncSession,
) -> tuple[SqlAlchemyScheduledTaskRepository, SqlAlchemyScheduledTaskExecutionRepository]:
    return (
        SqlAlchemyScheduledTaskRepository(session),
        SqlAlchemyScheduledTaskExecutionRepository(session),
    )


async def run_scheduler_tick(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    batch_limit: int,
    execution_timeout: timedelta = timedelta(seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS),
) -> SchedulerTickResult:
    result = SchedulerTickResult()
    await _settle_dispatched(
        session_factory,
        now=now,
        limit=batch_limit,
        result=result,
        execution_timeout=execution_timeout,
    )
    await _dispatch_pending(session_factory, now=now, limit=batch_limit, result=result)
    await _claim_due_tasks(session_factory, now=now, limit=batch_limit, result=result)
    return result


async def _settle_dispatched(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int,
    result: SchedulerTickResult,
    execution_timeout: timedelta,
) -> None:
    async with session_factory() as session:
        candidates = await SqlAlchemyScheduledTaskExecutionRepository(session).list_dispatched(
            limit=limit
        )
    for candidate in candidates:
        try:
            async with session_factory() as session:
                if await _settle_one(
                    session,
                    execution=candidate,
                    now=now,
                    execution_timeout=execution_timeout,
                ):
                    result.settled += 1
                await session.commit()
        except Exception:
            result.failed += 1
            result.failures.append(str(candidate.id))
            logger.exception(
                "scheduled_task_settle_failed", extra={"execution_id": str(candidate.id)}
            )


async def _settle_one(
    session: AsyncSession,
    *,
    execution: ScheduledTaskExecution,
    now: datetime,
    execution_timeout: timedelta,
) -> bool:
    tasks, executions = _repositories(session)
    if execution.run_id is None:
        # DISPATCHED 的执行必然带 run_id（`dispatched()` 保证），当前不可达，防御性分支。
        # 前向门禁：runs 的 FK 是 ondelete=SET NULL，将来一旦引入 Run 删除/保留清理，
        # 被删 Run 的执行会落到这里并永久停在 DISPATCHED（超时也兜不住），G1 的永久
        # 静默停摆会原样复发。届时必须一并处理（未必是加超时——级联删执行记录、或
        # 删 Run 时就地结算执行，可能是更好的答案）。见 roadmap C20 门禁来源说明。
        return False
    run = await SqlAlchemyRunRepository(session).get(
        tenant_id=execution.tenant_id, run_id=execution.run_id
    )
    if run is None:
        # run 行已消失而 run_id 仍非空：没有任何东西会再让它终态。当前全仓没有删除
        # Run 的代码路径，且 FK 的 SET NULL 恰恰阻止这种形态（它产生的是 run_id IS
        # NULL，命中上面那个更早的分支），因此这也是防御性分支、当前不可达。
        return await _settle_if_timed_out(
            session,
            execution=execution,
            now=now,
            execution_timeout=execution_timeout,
            run_status=None,
        )
    if run.status not in _TERMINAL_RUN_SETTLEMENTS:
        if run.status not in _UNTERMINATED_RUN_STATUSES:
            # 另有终结者：不误杀，等它自己走到终态再结算。
            return False
        return await _settle_if_timed_out(
            session,
            execution=execution,
            now=now,
            execution_timeout=execution_timeout,
            run_status=run.status,
        )
    task = await tasks.get(
        tenant_id=execution.tenant_id, task_id=execution.scheduled_task_id
    )
    if task is None:
        return False

    settled = _settlement_for(execution=execution, run=run, task=task, now=now)
    return bool(
        await executions.update_with_cas(
            settled, expected_revision=execution.revision
        )
    )


async def _settle_if_timed_out(
    session: AsyncSession,
    *,
    execution: ScheduledTaskExecution,
    now: datetime,
    execution_timeout: timedelta,
    run_status: RunStatus | None,
) -> bool:
    """给「无人终结」的执行封顶，避免定时任务被一条执行永久静默堵死。

    只在 `_UNTERMINATED_RUN_STATUSES`（以及 run 引用已丢失）时调用——调用方负责
    把另有终结者的状态挡在外面。超时只结算**调度侧**的执行记录，不去动 Run 本身
    （那是 C05/C13 的语义边界），也不触发重试。

    已知副作用（如实记录，非疏漏）：结算解除了 `list_active_for_task` 闸门，因此
    **超时之后 `ConcurrencyPolicy.SKIP` 不再保证同一时刻只有一个 Run**——被卡住的
    Run 可能仍活着，而下一个触发点会正常派发。这是刻意取舍：替代方案是任务永久
    静默停摆，明显更糟。见 roadmap C12「已知局限」。
    """

    if now - execution.updated_at <= execution_timeout:
        return False
    _, executions = _repositories(session)
    timed_out = execution.failed(
        now=now,
        error_message=f"execution_timed_out_after_{int(execution_timeout.total_seconds())}s",
    )
    if not await executions.update_with_cas(
        timed_out, expected_revision=execution.revision
    ):
        return False
    logger.warning(
        "scheduled_task_execution_timed_out",
        extra={
            "execution_id": str(execution.id),
            "scheduled_task_id": str(execution.scheduled_task_id),
            "run_id": str(execution.run_id) if execution.run_id else None,
        },
    )
    # 与派发/自动暂停一致：改状态并解除并发闸门的变更必须留痕。
    await emit_audit_event(
        session,
        tenant_id=execution.tenant_id,
        actor_user_id=None,
        action="scheduled_task.execution_timed_out",
        resource_type="scheduled_task",
        resource_id=execution.scheduled_task_id,
        metadata={
            "execution_id": str(execution.id),
            "run_id": str(execution.run_id) if execution.run_id else None,
            "run_status": run_status.value if run_status is not None else None,
            "timeout_seconds": int(execution_timeout.total_seconds()),
        },
    )
    return True


def _settlement_for(
    *,
    execution: ScheduledTaskExecution,
    run: Run,
    task: ScheduledTask,
    now: datetime,
) -> ScheduledTaskExecution:
    if run.status is RunStatus.COMPLETED:
        return execution.succeeded(now=now)
    if run.status is RunStatus.CANCELLED:
        return execution.cancelled(now=now)
    delay = execution.retry_delay(
        max_retries=task.max_retries, retry_backoff_seconds=task.retry_backoff_seconds
    )
    if delay is None:
        return execution.failed(now=now, error_message=run.error_message)
    return execution.awaiting_retry(
        next_attempt_at=now + delay, now=now, error_message=run.error_message
    )


async def _dispatch_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int,
    result: SchedulerTickResult,
) -> None:
    async with session_factory() as session:
        candidates = await SqlAlchemyScheduledTaskExecutionRepository(
            session
        ).list_pending_dispatch(now=now, limit=limit)
    for candidate in candidates:
        try:
            async with session_factory() as session:
                outcome = await _dispatch_pending_one(
                    session, execution=candidate, now=now
                )
                if outcome is _PendingOutcome.DISPATCHED:
                    result.dispatched += 1
                elif outcome is _PendingOutcome.SKIPPED:
                    result.skipped += 1
                await session.commit()
        except Exception:
            result.failed += 1
            result.failures.append(str(candidate.id))
            logger.exception(
                "scheduled_task_pending_dispatch_failed",
                extra={"execution_id": str(candidate.id)},
            )


async def _dispatch_pending_one(
    session: AsyncSession, *, execution: ScheduledTaskExecution, now: datetime
) -> _PendingOutcome:
    tasks, executions = _repositories(session)
    task = await tasks.lock_task(task_id=execution.scheduled_task_id)
    if task is None:
        return _PendingOutcome.NOOP
    # 拿到任务行锁后重新读执行记录：认领与结算之间的窗口不能凭旧快照决策（TOCTOU）。
    current = await executions.get(
        tenant_id=execution.tenant_id, execution_id=execution.id
    )
    if current is None:
        return _PendingOutcome.NOOP
    # 状态守卫必须排在暂停判定之前：扫描到写入之间该执行可能已被别的副本推进
    # （DEFERRED→DISPATCHED），对非待派发状态调 skipped() 会抛非法转换，被上层宽
    # except 接住后计入 failed 并误报告警——那正是 A-3 要消灭的「良性竞态报成真失败」。
    if current.status is ExecutionStatus.RETRY_WAITING:
        if current.next_attempt_at is None or current.next_attempt_at > now:
            return _PendingOutcome.NOOP
    elif current.status is not ExecutionStatus.DEFERRED:
        # 已被其他副本推进或已终态：本跳无事可做，既不是失败也不是业务跳过。
        return _PendingOutcome.NOOP

    if not task.enabled:
        # 用户已暂停：排队中的触发点与等待中的重试都不得再起 Run。就地结算成终态，
        # 否则它们会每一跳被重新捞出，白占派发预算且永远不结算。
        await executions.update_with_cas(
            current.skipped(reason=SkipReason.TASK_PAUSED, now=now),
            expected_revision=current.revision,
        )
        return _PendingOutcome.SKIPPED

    if current.status is ExecutionStatus.DEFERRED and await _has_other_active(
        executions, task_id=task.id, exclude_id=current.id
    ):
        # 上一轮仍在跑：继续排队等下一跳，不是业务跳过。
        return _PendingOutcome.NOOP
    dispatched = (
        await _dispatch_execution(session, task=task, execution=current, now=now) is None
    )
    return _PendingOutcome.DISPATCHED if dispatched else _PendingOutcome.SKIPPED


async def _has_other_active(
    executions: SqlAlchemyScheduledTaskExecutionRepository, *, task_id: UUID, exclude_id: UUID
) -> bool:
    active: Sequence[ScheduledTaskExecution] = await executions.list_active_for_task(
        scheduled_task_id=task_id
    )
    return any(
        item.id != exclude_id and item.status is not ExecutionStatus.DEFERRED
        for item in active
    )


async def _claim_due_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int,
    result: SchedulerTickResult,
) -> None:
    async with session_factory() as session:
        candidate_ids = await SqlAlchemyScheduledTaskRepository(session).list_due_task_ids(
            now=now, limit=limit
        )
    for task_id in candidate_ids:
        try:
            async with session_factory() as session:
                await _claim_one(session, task_id=task_id, now=now, result=result)
                await session.commit()
        except Exception:
            result.failed += 1
            result.failures.append(str(task_id))
            logger.exception("scheduled_task_claim_failed", extra={"task_id": str(task_id)})


async def _claim_one(
    session: AsyncSession,
    *,
    task_id: UUID,
    now: datetime,
    result: SchedulerTickResult,
) -> None:
    tasks, executions = _repositories(session)
    task = await tasks.lock_due_task(task_id=task_id, now=now)
    if task is None:
        # 已被其他副本认领（SKIP LOCKED）或已不再到期。
        return
    plan = plan_trigger(task, now=now)
    if plan.outcome is TriggerOutcome.IDLE or plan.scheduled_for is None:
        return

    if plan.outcome is TriggerOutcome.SKIP:
        assert plan.skip_reason is not None
        await _record_skip(
            session, task=task, scheduled_for=plan.scheduled_for, reason=plan.skip_reason, now=now
        )
        await _advance(session, task=task, next_run_at=plan.next_run_at, now=now)
        result.skipped += 1
        return

    contention = await _resolve_contention(executions, task=task)
    if contention is not None:
        if contention is _Contention.ENQUEUE:
            await _enqueue(session, task=task, scheduled_for=plan.scheduled_for, now=now)
        else:
            await _record_skip(
                session,
                task=task,
                scheduled_for=plan.scheduled_for,
                reason=contention.skip_reason,
                now=now,
            )
        await _advance(session, task=task, next_run_at=plan.next_run_at, now=now)
        result.skipped += 1
        return

    execution = ScheduledTaskExecution.create(
        tenant_id=task.tenant_id,
        scheduled_task_id=task.id,
        scheduled_for=plan.scheduled_for,
        status=ExecutionStatus.DEFERRED,
        now=now,
    )
    # 先落触发点再建 Run：只有这一条语句可能合法地撞上触发点唯一索引，因此冲突判定
    # 只包住它——套在整个 _claim_one 外面会把 Run/命令/审计的完整性故障也误判成
    # 「另一个副本抢先了」而静默丢弃。savepoint 让冲突回滚后外层事务仍可提交。
    try:
        async with session.begin_nested():
            await executions.add(execution)
            await session.flush()
    except IntegrityError:
        logger.info(
            "scheduled_task_trigger_already_claimed",
            extra={"task_id": str(task.id), "scheduled_for": plan.scheduled_for.isoformat()},
        )
        return

    guard = await _dispatch_execution(session, task=task, execution=execution, now=now)
    if guard is None:
        await _mark_dispatched(session, task=task, next_run_at=plan.next_run_at, now=now)
        result.dispatched += 1
        return
    result.skipped += 1
    if pause_reason_for_guard(guard) is not None:
        # 守卫已把任务自动暂停：绝不能再用暂停前的旧快照 _advance 把它写回 enabled。
        # （此前靠 _advance 的 CAS 恰好失败才没出事，属于巧合正确。）
        return
    await _advance(session, task=task, next_run_at=plan.next_run_at, now=now)


class _PendingOutcome(StrEnum):
    """待派发执行这一跳的结果；NOOP 是良性竞态，不该计入任何业务指标。"""

    DISPATCHED = "dispatched"
    SKIPPED = "skipped"
    NOOP = "noop"


class _Contention(StrEnum):
    """上一轮仍活跃时，本次触发点的处置。"""

    ENQUEUE = "enqueue"
    SKIP = "skip"
    COLLAPSE = "collapse"

    @property
    def skip_reason(self) -> SkipReason:
        return (
            SkipReason.QUEUE_COLLAPSED
            if self is _Contention.COLLAPSE
            else SkipReason.CONCURRENCY_SKIPPED
        )


async def _resolve_contention(
    executions: SqlAlchemyScheduledTaskExecutionRepository, *, task: ScheduledTask
) -> _Contention | None:
    """None 表示没有争用、可以正常派发。"""

    if task.concurrency_policy is ConcurrencyPolicy.ALLOW:
        return None
    active: Sequence[ScheduledTaskExecution] = await executions.list_active_for_task(
        scheduled_task_id=task.id
    )
    if not active:
        return None
    if task.concurrency_policy is ConcurrencyPolicy.SKIP:
        return _Contention.SKIP
    # QUEUE：队列深度恒为 1，已有排队者时后续触发点合并丢弃，不无界堆积。
    if any(item.status is ExecutionStatus.DEFERRED for item in active):
        return _Contention.COLLAPSE
    return _Contention.ENQUEUE


async def _dispatch_execution(
    session: AsyncSession,
    *,
    task: ScheduledTask,
    execution: ScheduledTaskExecution,
    now: datetime,
) -> SkipReason | None:
    """守卫通过则创建正常 Run + START 命令，返回 None；否则受控跳过并返回跳过原因。

    返回原因而不是 bool，调用方才能区分「守卫失败且任务已被自动暂停」（不得再推进
    next_run_at）与「守卫失败但任务仍启用」（应推进到下一个触发点）。
    """

    _, executions = _repositories(session)
    context = await _load_dispatch_context(session, task=task)
    guard = evaluate_dispatch_guards(task, context)
    if guard is not None:
        await executions.update_with_cas(
            execution.skipped(reason=guard, now=now), expected_revision=execution.revision
        )
        await _auto_pause(session, task=task, reason=guard, now=now)
        return guard

    assert context.published_version is not None
    run = await create_employee_run(
        database_session=session,
        tenant_id=task.tenant_id,
        employee_id=task.employee_id,
        employee_version=context.published_version,
        created_by=task.created_by,
        input_data=task.input_data,
    )
    await executions.update_with_cas(
        execution.dispatched(run_id=run.id, now=now), expected_revision=execution.revision
    )
    await _audit(
        session,
        task=task,
        action="scheduled_task.dispatched",
        metadata={
            "run_id": str(run.id),
            "scheduled_for": execution.scheduled_for.isoformat(),
            "attempt": execution.attempts + 1,
        },
    )
    return None


async def _load_dispatch_context(
    session: AsyncSession, *, task: ScheduledTask
) -> DispatchContext:
    access = await SqlAlchemyWorkspaceRepository(session).get_for_user(
        user_id=task.created_by, tenant_id=task.tenant_id
    )
    employee = await SqlAlchemyEmployeeRepository(session).get(
        tenant_id=task.tenant_id, employee_id=task.employee_id
    )
    if employee is None or employee.published_version is None:
        return DispatchContext(
            published_version=None,
            definition=None,
            creator_role=access.role if access is not None else None,
        )
    version = await SqlAlchemyEmployeeVersionRepository(session).get(
        tenant_id=task.tenant_id,
        employee_id=task.employee_id,
        version=employee.published_version,
    )
    return DispatchContext(
        published_version=employee.published_version if version is not None else None,
        definition=version.definition if version is not None else None,
        creator_role=access.role if access is not None else None,
    )


async def _record_skip(
    session: AsyncSession,
    *,
    task: ScheduledTask,
    scheduled_for: datetime,
    reason: SkipReason,
    now: datetime,
) -> None:
    _, executions = _repositories(session)
    await executions.add(
        ScheduledTaskExecution.create(
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            scheduled_for=scheduled_for,
            status=ExecutionStatus.SKIPPED,
            skip_reason=reason,
            now=now,
        )
    )
    await session.flush()


async def _enqueue(
    session: AsyncSession, *, task: ScheduledTask, scheduled_for: datetime, now: datetime
) -> None:
    _, executions = _repositories(session)
    await executions.add(
        ScheduledTaskExecution.create(
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            scheduled_for=scheduled_for,
            status=ExecutionStatus.DEFERRED,
            now=now,
        )
    )
    await session.flush()


async def _advance(
    session: AsyncSession, *, task: ScheduledTask, next_run_at: datetime | None, now: datetime
) -> None:
    tasks, _ = _repositories(session)
    await tasks.update_with_cas(
        task.advance_to(next_run_at, now=now), expected_revision=task.revision
    )


async def _mark_dispatched(
    session: AsyncSession, *, task: ScheduledTask, next_run_at: datetime | None, now: datetime
) -> None:
    tasks, _ = _repositories(session)
    await tasks.update_with_cas(
        task.mark_dispatched(next_run_at=next_run_at, now=now),
        expected_revision=task.revision,
    )


async def _auto_pause(
    session: AsyncSession, *, task: ScheduledTask, reason: SkipReason, now: datetime
) -> None:
    """守卫失败必须暂停任务：否则每一跳都会重复跳过，历史无界增长。"""

    pause_reason = pause_reason_for_guard(reason)
    if pause_reason is None:
        return
    tasks, _ = _repositories(session)
    await tasks.update_with_cas(
        task.auto_pause(reason=pause_reason, now=now), expected_revision=task.revision
    )
    await _audit(
        session,
        task=task,
        action="scheduled_task.auto_paused",
        metadata={"reason": pause_reason.value},
    )


async def _audit(
    session: AsyncSession,
    *,
    task: ScheduledTask,
    action: str,
    metadata: dict[str, JsonValue],
) -> None:
    await emit_audit_event(
        session,
        tenant_id=task.tenant_id,
        actor_user_id=None,
        action=action,
        resource_type="scheduled_task",
        resource_id=task.id,
        metadata={"employee_id": str(task.employee_id), **metadata},
    )


async def purge_scheduled_task_executions(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    cutoff: datetime,
    limit: int,
) -> int:
    """清理过期的终态执行历史，保证历史不会无界增长。"""

    async with session_factory() as session:
        purged = await SqlAlchemyScheduledTaskExecutionRepository(session).purge_terminal_before(
            cutoff=cutoff, limit=limit
        )
        await session.commit()
    return purged
