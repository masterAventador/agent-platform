"""C12 执行记录状态机：派发、成功/失败/取消、重试退避与跳过。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.scheduling.entities import (
    ACTIVE_EXECUTION_STATUSES,
    MAX_RETRY_BACKOFF_SECONDS,
    TERMINAL_EXECUTION_STATUSES,
    ExecutionStatus,
    ScheduledTaskExecution,
    SkipReason,
)
from agent_platform.platform.scheduling.errors import (
    InvalidScheduledTaskExecutionTransition,
)

NOW = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def build_execution(status: ExecutionStatus = ExecutionStatus.DEFERRED) -> ScheduledTaskExecution:
    return ScheduledTaskExecution.create(
        tenant_id=uuid4(),
        scheduled_task_id=uuid4(),
        scheduled_for=NOW,
        status=status,
        now=NOW,
    )


def test_dispatch_binds_a_run_and_counts_the_attempt() -> None:
    execution = build_execution()
    run_id = uuid4()

    dispatched = execution.dispatched(run_id=run_id, now=NOW)

    assert dispatched.status is ExecutionStatus.DISPATCHED
    assert dispatched.run_id == run_id
    assert dispatched.attempts == 1
    assert dispatched.revision == 2


def test_dispatched_execution_settles_to_succeeded_cancelled_or_failed() -> None:
    dispatched = build_execution().dispatched(run_id=uuid4(), now=NOW)

    assert dispatched.succeeded(now=NOW).status is ExecutionStatus.SUCCEEDED
    assert dispatched.cancelled(now=NOW).status is ExecutionStatus.CANCELLED

    failed = dispatched.failed(now=NOW, error_message="模型超时")
    assert failed.status is ExecutionStatus.FAILED
    assert failed.error_message == "模型超时"


def test_terminal_executions_reject_every_further_transition() -> None:
    dispatched = build_execution().dispatched(run_id=uuid4(), now=NOW)

    for terminal in (
        dispatched.succeeded(now=NOW),
        dispatched.failed(now=NOW),
        dispatched.cancelled(now=NOW),
        build_execution().skipped(reason=SkipReason.CONCURRENCY_SKIPPED, now=NOW),
    ):
        assert terminal.status in TERMINAL_EXECUTION_STATUSES
        with pytest.raises(InvalidScheduledTaskExecutionTransition):
            terminal.succeeded(now=NOW)
        with pytest.raises(InvalidScheduledTaskExecutionTransition):
            terminal.dispatched(run_id=uuid4(), now=NOW)
        with pytest.raises(InvalidScheduledTaskExecutionTransition):
            terminal.skipped(reason=SkipReason.CONCURRENCY_SKIPPED, now=NOW)


def test_a_dispatched_execution_cannot_be_skipped() -> None:
    dispatched = build_execution().dispatched(run_id=uuid4(), now=NOW)

    with pytest.raises(InvalidScheduledTaskExecutionTransition):
        dispatched.skipped(reason=SkipReason.CONCURRENCY_SKIPPED, now=NOW)


def test_retry_waiting_can_be_dispatched_again_and_keeps_counting_attempts() -> None:
    dispatched = build_execution().dispatched(run_id=uuid4(), now=NOW)
    waiting = dispatched.awaiting_retry(
        next_attempt_at=NOW + timedelta(seconds=60), now=NOW, error_message="模型超时"
    )

    assert waiting.status is ExecutionStatus.RETRY_WAITING
    assert waiting.next_attempt_at == NOW + timedelta(seconds=60)

    retried = waiting.dispatched(run_id=uuid4(), now=NOW)
    assert retried.attempts == 2
    assert retried.next_attempt_at is None


def test_retry_delay_grows_exponentially_and_stops_when_attempts_are_used_up() -> None:
    execution = build_execution().dispatched(run_id=uuid4(), now=NOW)

    assert execution.retry_delay(max_retries=2, retry_backoff_seconds=30) == timedelta(seconds=30)

    second = execution.awaiting_retry(next_attempt_at=NOW, now=NOW).dispatched(
        run_id=uuid4(), now=NOW
    )
    assert second.retry_delay(max_retries=2, retry_backoff_seconds=30) == timedelta(seconds=60)

    third = second.awaiting_retry(next_attempt_at=NOW, now=NOW).dispatched(run_id=uuid4(), now=NOW)
    assert third.attempts == 3
    assert third.retry_delay(max_retries=2, retry_backoff_seconds=30) is None


def test_retry_delay_is_capped_so_backoff_cannot_grow_without_bound() -> None:
    execution = build_execution()
    for _ in range(20):
        execution = execution.dispatched(run_id=uuid4(), now=NOW).awaiting_retry(
            next_attempt_at=NOW, now=NOW
        )
    execution = execution.dispatched(run_id=uuid4(), now=NOW)

    assert execution.retry_delay(max_retries=100, retry_backoff_seconds=60) == timedelta(
        seconds=MAX_RETRY_BACKOFF_SECONDS
    )


def test_no_retry_is_offered_when_retries_are_disabled() -> None:
    execution = build_execution().dispatched(run_id=uuid4(), now=NOW)

    assert execution.retry_delay(max_retries=0, retry_backoff_seconds=60) is None


def test_deferred_execution_is_active_and_can_be_skipped_or_dispatched() -> None:
    execution = build_execution(ExecutionStatus.DEFERRED)

    assert execution.status in ACTIVE_EXECUTION_STATUSES
    assert execution.dispatched(run_id=uuid4(), now=NOW).status is ExecutionStatus.DISPATCHED
    assert (
        execution.skipped(reason=SkipReason.QUEUE_COLLAPSED, now=NOW).skip_reason
        is SkipReason.QUEUE_COLLAPSED
    )
