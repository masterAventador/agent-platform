"""C12 定时与预约任务的领域实体与调度决策规则。

定时任务只描述“何时、以什么输入、代表谁去跑哪个数字员工”；真正的执行仍是平台
既有的 Run + START 命令，由既有 Dispatcher/Worker 消费——调度不建立旁路执行体系。

两条状态线：
- ScheduledTask：enabled/paused 与 next_run_at（下次触发点，UTC）；
- ScheduledTaskExecution：单个触发点的结果，deferred/dispatched/retry_waiting
  -> succeeded/failed/cancelled/skipped，终态封死。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import JsonValue

from agent_platform.platform.scheduling.errors import (
    InvalidScheduledTaskExecutionTransition,
    InvalidScheduledTaskTransition,
)
from agent_platform.platform.scheduling.schedule import Schedule, ScheduleKind

DEFAULT_MISFIRE_GRACE_SECONDS = 60
DEFAULT_MISFIRE_BACKFILL_WINDOW_SECONDS = 24 * 60 * 60
DEFAULT_MAX_RETRIES = 0
DEFAULT_RETRY_BACKOFF_SECONDS = 60
MAX_RETRY_BACKOFF_SECONDS = 24 * 60 * 60


class MisfirePolicy(StrEnum):
    """进程停机期间错过的触发点如何处理。"""

    SKIP = "skip"
    RUN_ONCE = "run_once"
    RUN_ALL = "run_all"


class ConcurrencyPolicy(StrEnum):
    """上一轮仍在跑时到达下一个触发点如何处理。"""

    ALLOW = "allow"
    SKIP = "skip"
    QUEUE = "queue"


class PauseReason(StrEnum):
    """自动暂停的机器可读原因；人工暂停不带原因。"""

    EMPLOYEE_NOT_RUNNABLE = "employee_not_runnable"
    SCHEDULED_TASKS_DISABLED = "scheduled_tasks_disabled"
    CREATOR_PERMISSION_REVOKED = "creator_permission_revoked"
    INPUT_SCHEMA_INCOMPATIBLE = "input_schema_incompatible"


class SkipReason(StrEnum):
    """某个触发点没有产生 Run 的原因（都会留下可见的执行历史）。"""

    TASK_PAUSED = "task_paused"
    MISFIRE_SKIPPED = "misfire_skipped"
    MISFIRE_WINDOW_EXCEEDED = "misfire_window_exceeded"
    CONCURRENCY_SKIPPED = "concurrency_skipped"
    QUEUE_COLLAPSED = "queue_collapsed"
    EMPLOYEE_NOT_RUNNABLE = "employee_not_runnable"
    SCHEDULED_TASKS_DISABLED = "scheduled_tasks_disabled"
    CREATOR_PERMISSION_REVOKED = "creator_permission_revoked"
    INPUT_SCHEMA_INCOMPATIBLE = "input_schema_incompatible"


_GUARD_PAUSE_REASONS: dict[SkipReason, PauseReason] = {
    SkipReason.EMPLOYEE_NOT_RUNNABLE: PauseReason.EMPLOYEE_NOT_RUNNABLE,
    SkipReason.SCHEDULED_TASKS_DISABLED: PauseReason.SCHEDULED_TASKS_DISABLED,
    SkipReason.CREATOR_PERMISSION_REVOKED: PauseReason.CREATOR_PERMISSION_REVOKED,
    SkipReason.INPUT_SCHEMA_INCOMPATIBLE: PauseReason.INPUT_SCHEMA_INCOMPATIBLE,
}


def pause_reason_for_guard(reason: SkipReason) -> PauseReason | None:
    """守卫类跳过（权限/员工状态）会自动暂停任务，避免无界地反复跳过。"""

    return _GUARD_PAUSE_REASONS.get(reason)


class ExecutionStatus(StrEnum):
    DEFERRED = "deferred"
    DISPATCHED = "dispatched"
    RETRY_WAITING = "retry_waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


ACTIVE_EXECUTION_STATUSES = frozenset(
    {ExecutionStatus.DEFERRED, ExecutionStatus.DISPATCHED, ExecutionStatus.RETRY_WAITING}
)
TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.SKIPPED,
    }
)

_ALLOWED_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.DEFERRED: frozenset({ExecutionStatus.DISPATCHED, ExecutionStatus.SKIPPED}),
    ExecutionStatus.DISPATCHED: frozenset(
        {
            ExecutionStatus.RETRY_WAITING,
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }
    ),
    ExecutionStatus.RETRY_WAITING: frozenset(
        {
            ExecutionStatus.DISPATCHED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            # 任务被暂停时，等待重试的执行必须能就地结算，不能一直挂着被反复扫描。
            ExecutionStatus.SKIPPED,
        }
    ),
    ExecutionStatus.SUCCEEDED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.SKIPPED: frozenset(),
}


class TriggerOutcome(StrEnum):
    IDLE = "idle"
    DISPATCH = "dispatch"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class TriggerPlan:
    """对单个到期判定的决策：跑、跳过还是无事可做，以及新的 next_run_at。"""

    outcome: TriggerOutcome
    next_run_at: datetime | None = None
    scheduled_for: datetime | None = None
    skip_reason: SkipReason | None = None


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    created_by: UUID
    name: str
    schedule: Schedule
    input_data: dict[str, JsonValue]
    enabled: bool
    next_run_at: datetime | None
    misfire_policy: MisfirePolicy
    concurrency_policy: ConcurrencyPolicy
    misfire_grace_seconds: int
    misfire_backfill_window_seconds: int
    max_retries: int
    retry_backoff_seconds: int
    revision: int
    created_at: datetime
    updated_at: datetime
    pause_reason: PauseReason | None = None
    last_run_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        created_by: UUID,
        name: str,
        schedule: Schedule,
        input_data: dict[str, JsonValue],
        now: datetime,
        misfire_policy: MisfirePolicy = MisfirePolicy.SKIP,
        concurrency_policy: ConcurrencyPolicy = ConcurrencyPolicy.SKIP,
        misfire_grace_seconds: int = DEFAULT_MISFIRE_GRACE_SECONDS,
        misfire_backfill_window_seconds: int = DEFAULT_MISFIRE_BACKFILL_WINDOW_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
        task_id: UUID | None = None,
    ) -> ScheduledTask:
        return cls(
            id=task_id or uuid4(),
            tenant_id=tenant_id,
            employee_id=employee_id,
            created_by=created_by,
            name=name,
            schedule=schedule,
            input_data=input_data,
            enabled=True,
            next_run_at=schedule.next_occurrence_after(now),
            misfire_policy=misfire_policy,
            concurrency_policy=concurrency_policy,
            misfire_grace_seconds=misfire_grace_seconds,
            misfire_backfill_window_seconds=misfire_backfill_window_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            revision=1,
            created_at=now,
            updated_at=now,
        )

    @property
    def is_exhausted(self) -> bool:
        """启用中但已没有未来触发点（单次预约已过期/已触发）。"""

        return self.enabled and self.next_run_at is None

    def pause(self, *, now: datetime) -> ScheduledTask:
        if not self.enabled:
            raise InvalidScheduledTaskTransition("任务已处于暂停状态")
        return self._paused(reason=None, now=now)

    def auto_pause(self, *, reason: PauseReason, now: datetime) -> ScheduledTask:
        """守卫失败时由调度器暂停；已暂停的任务只更新原因，不算非法转换。"""

        return self._paused(reason=reason, now=now)

    def resume(self, *, now: datetime) -> ScheduledTask:
        if self.enabled:
            raise InvalidScheduledTaskTransition("任务已处于启用状态")
        next_run_at = self.schedule.next_occurrence_after(now)
        if next_run_at is None:
            raise InvalidScheduledTaskTransition("该调度已没有未来触发点，无法恢复")
        return replace(
            self,
            enabled=True,
            pause_reason=None,
            next_run_at=next_run_at,
            updated_at=now,
            revision=self.revision + 1,
        )

    def reschedule(
        self,
        *,
        schedule: Schedule,
        input_data: dict[str, JsonValue],
        name: str,
        misfire_policy: MisfirePolicy,
        concurrency_policy: ConcurrencyPolicy,
        max_retries: int,
        retry_backoff_seconds: int,
        now: datetime,
    ) -> ScheduledTask:
        """编辑任务；启用中的任务按新调度重算 next_run_at，暂停中的保持暂停。"""

        return replace(
            self,
            name=name,
            schedule=schedule,
            input_data=input_data,
            misfire_policy=misfire_policy,
            concurrency_policy=concurrency_policy,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            next_run_at=schedule.next_occurrence_after(now) if self.enabled else None,
            updated_at=now,
            revision=self.revision + 1,
        )

    def advance_to(self, next_run_at: datetime | None, *, now: datetime) -> ScheduledTask:
        return replace(
            self,
            next_run_at=next_run_at,
            updated_at=now,
            revision=self.revision + 1,
        )

    def mark_dispatched(self, *, next_run_at: datetime | None, now: datetime) -> ScheduledTask:
        return replace(
            self,
            next_run_at=next_run_at,
            last_run_at=now,
            updated_at=now,
            revision=self.revision + 1,
        )

    def _paused(self, *, reason: PauseReason | None, now: datetime) -> ScheduledTask:
        return replace(
            self,
            enabled=False,
            pause_reason=reason,
            next_run_at=None,
            updated_at=now,
            revision=self.revision + 1,
        )


def plan_trigger(task: ScheduledTask, *, now: datetime) -> TriggerPlan:
    """判定一个到期的定时任务这一跳该做什么。

    misfire 判定只依赖 next_run_at 与 now，不回溯枚举错过的触发点——按分钟级
    Cron 停机一年会有数十万个触发点，枚举本身就是无界成本。
    """

    if not task.enabled or task.next_run_at is None or task.next_run_at > now:
        return TriggerPlan(outcome=TriggerOutcome.IDLE)

    scheduled_for = task.next_run_at
    lateness = now - scheduled_for
    if lateness <= timedelta(seconds=task.misfire_grace_seconds):
        return TriggerPlan(
            outcome=TriggerOutcome.DISPATCH,
            scheduled_for=scheduled_for,
            next_run_at=task.schedule.next_occurrence_after(scheduled_for),
        )

    if task.misfire_policy is MisfirePolicy.SKIP:
        return TriggerPlan(
            outcome=TriggerOutcome.SKIP,
            scheduled_for=scheduled_for,
            skip_reason=SkipReason.MISFIRE_SKIPPED,
            next_run_at=task.schedule.next_occurrence_after(now),
        )

    if task.misfire_policy is MisfirePolicy.RUN_ONCE:
        return TriggerPlan(
            outcome=TriggerOutcome.DISPATCH,
            scheduled_for=scheduled_for,
            next_run_at=task.schedule.next_occurrence_after(now),
        )

    backfill_window = timedelta(seconds=task.misfire_backfill_window_seconds)
    if lateness > backfill_window:
        # 补跑窗口之外的触发点一次性丢弃：否则停机越久补跑的 Run 越多，成本无界。
        return TriggerPlan(
            outcome=TriggerOutcome.SKIP,
            scheduled_for=scheduled_for,
            skip_reason=SkipReason.MISFIRE_WINDOW_EXCEEDED,
            next_run_at=task.schedule.next_occurrence_after(now - backfill_window),
        )
    # 窗口内逐个补跑：每跳补一个触发点，下一跳继续，直到追平。
    return TriggerPlan(
        outcome=TriggerOutcome.DISPATCH,
        scheduled_for=scheduled_for,
        next_run_at=task.schedule.next_occurrence_after(scheduled_for),
    )


@dataclass(frozen=True, slots=True)
class ScheduledTaskExecution:
    id: UUID
    tenant_id: UUID
    scheduled_task_id: UUID
    scheduled_for: datetime
    status: ExecutionStatus
    attempts: int
    revision: int
    created_at: datetime
    updated_at: datetime
    run_id: UUID | None = None
    skip_reason: SkipReason | None = None
    error_message: str | None = None
    next_attempt_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        scheduled_task_id: UUID,
        scheduled_for: datetime,
        status: ExecutionStatus,
        now: datetime,
        run_id: UUID | None = None,
        skip_reason: SkipReason | None = None,
        attempts: int = 0,
        execution_id: UUID | None = None,
    ) -> ScheduledTaskExecution:
        return cls(
            id=execution_id or uuid4(),
            tenant_id=tenant_id,
            scheduled_task_id=scheduled_task_id,
            scheduled_for=scheduled_for,
            status=status,
            attempts=attempts,
            revision=1,
            created_at=now,
            updated_at=now,
            run_id=run_id,
            skip_reason=skip_reason,
        )

    def dispatched(self, *, run_id: UUID, now: datetime) -> ScheduledTaskExecution:
        self._ensure_transition(ExecutionStatus.DISPATCHED)
        return replace(
            self,
            status=ExecutionStatus.DISPATCHED,
            run_id=run_id,
            attempts=self.attempts + 1,
            next_attempt_at=None,
            updated_at=now,
            revision=self.revision + 1,
        )

    def succeeded(self, *, now: datetime) -> ScheduledTaskExecution:
        return self._settle(ExecutionStatus.SUCCEEDED, now=now)

    def cancelled(self, *, now: datetime) -> ScheduledTaskExecution:
        return self._settle(ExecutionStatus.CANCELLED, now=now)

    def failed(
        self, *, now: datetime, error_message: str | None = None
    ) -> ScheduledTaskExecution:
        return replace(
            self._settle(ExecutionStatus.FAILED, now=now),
            error_message=error_message,
        )

    def awaiting_retry(
        self,
        *,
        next_attempt_at: datetime,
        now: datetime,
        error_message: str | None = None,
    ) -> ScheduledTaskExecution:
        self._ensure_transition(ExecutionStatus.RETRY_WAITING)
        return replace(
            self,
            status=ExecutionStatus.RETRY_WAITING,
            next_attempt_at=next_attempt_at,
            error_message=error_message,
            updated_at=now,
            revision=self.revision + 1,
        )

    def skipped(self, *, reason: SkipReason, now: datetime) -> ScheduledTaskExecution:
        self._ensure_transition(ExecutionStatus.SKIPPED)
        return replace(
            self,
            status=ExecutionStatus.SKIPPED,
            skip_reason=reason,
            updated_at=now,
            revision=self.revision + 1,
        )

    def retry_delay(self, *, max_retries: int, retry_backoff_seconds: int) -> timedelta | None:
        """本次失败后的重试退避（指数、带上限）；重试次数用尽时返回 None。"""

        if self.attempts > max_retries:
            return None
        return timedelta(
            seconds=min(
                retry_backoff_seconds * (2 ** (self.attempts - 1)),
                MAX_RETRY_BACKOFF_SECONDS,
            )
        )

    def _settle(self, status: ExecutionStatus, *, now: datetime) -> ScheduledTaskExecution:
        self._ensure_transition(status)
        return replace(
            self,
            status=status,
            next_attempt_at=None,
            updated_at=now,
            revision=self.revision + 1,
        )

    def _ensure_transition(self, status: ExecutionStatus) -> None:
        if status not in _ALLOWED_EXECUTION_TRANSITIONS[self.status]:
            raise InvalidScheduledTaskExecutionTransition(
                f"{self.status.value} -> {status.value}"
            )


def is_scheduling_enabled(capabilities: object) -> bool:
    """发布版本是否开启了定时任务能力（按发布版本快照解释，不看草稿）。"""

    return isinstance(capabilities, dict) and capabilities.get("scheduled_tasks") is True


__all__ = [
    "ACTIVE_EXECUTION_STATUSES",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MISFIRE_BACKFILL_WINDOW_SECONDS",
    "DEFAULT_MISFIRE_GRACE_SECONDS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "MAX_RETRY_BACKOFF_SECONDS",
    "TERMINAL_EXECUTION_STATUSES",
    "ExecutionStatus",
    "ConcurrencyPolicy",
    "MisfirePolicy",
    "PauseReason",
    "Schedule",
    "ScheduleKind",
    "ScheduledTask",
    "ScheduledTaskExecution",
    "SkipReason",
    "TriggerOutcome",
    "TriggerPlan",
    "is_scheduling_enabled",
    "pause_reason_for_guard",
    "plan_trigger",
]
