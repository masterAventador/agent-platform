"""C12 定时任务实体：启停状态机、下次执行时间与错过执行（misfire）策略。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.scheduling.entities import (
    DEFAULT_MISFIRE_GRACE_SECONDS,
    ConcurrencyPolicy,
    MisfirePolicy,
    PauseReason,
    ScheduledTask,
    SkipReason,
    TriggerOutcome,
    plan_trigger,
)
from agent_platform.platform.scheduling.errors import InvalidScheduledTaskTransition
from agent_platform.platform.scheduling.schedule import Schedule

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)


def build_task(
    *,
    schedule: Schedule | None = None,
    now: datetime = NOW,
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP,
    concurrency_policy: ConcurrencyPolicy = ConcurrencyPolicy.SKIP,
) -> ScheduledTask:
    return ScheduledTask.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        created_by=uuid4(),
        name="每小时巡检",
        schedule=schedule or Schedule.cron(expression="0 * * * *", timezone="UTC"),
        input_data={"topic": "巡检"},
        misfire_policy=misfire_policy,
        concurrency_policy=concurrency_policy,
        now=now,
    )


def test_created_task_is_enabled_and_has_the_next_occurrence_after_now() -> None:
    task = build_task()

    assert task.enabled is True
    assert task.pause_reason is None
    assert task.revision == 1
    assert task.next_run_at == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_created_one_shot_task_targets_its_reserved_instant() -> None:
    run_at = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    task = build_task(schedule=Schedule.once(run_at=run_at, timezone="UTC"))

    assert task.next_run_at == run_at


def test_creating_a_one_shot_task_in_the_past_leaves_nothing_to_run() -> None:
    task = build_task(
        schedule=Schedule.once(run_at=NOW - timedelta(hours=1), timezone="UTC")
    )

    assert task.next_run_at is None
    assert task.is_exhausted is True


def test_pause_clears_the_next_run_and_resume_recomputes_it() -> None:
    task = build_task()

    paused = task.pause(now=NOW)
    assert paused.enabled is False
    assert paused.next_run_at is None
    assert paused.revision == 2

    resumed = paused.resume(now=datetime(2026, 7, 17, 10, 30, tzinfo=UTC))
    assert resumed.enabled is True
    assert resumed.pause_reason is None
    assert resumed.next_run_at == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert resumed.revision == 3


def test_pausing_a_paused_task_and_resuming_a_running_task_are_rejected() -> None:
    task = build_task()

    with pytest.raises(InvalidScheduledTaskTransition):
        task.resume(now=NOW)

    paused = task.pause(now=NOW)
    with pytest.raises(InvalidScheduledTaskTransition):
        paused.pause(now=NOW)


def test_resuming_an_exhausted_one_shot_task_is_rejected() -> None:
    task = build_task(schedule=Schedule.once(run_at=NOW + timedelta(hours=1), timezone="UTC"))
    paused = task.pause(now=NOW)

    with pytest.raises(InvalidScheduledTaskTransition):
        paused.resume(now=NOW + timedelta(hours=2))


def test_auto_pause_records_a_machine_readable_reason() -> None:
    task = build_task()

    paused = task.auto_pause(reason=PauseReason.EMPLOYEE_NOT_RUNNABLE, now=NOW)

    assert paused.enabled is False
    assert paused.next_run_at is None
    assert paused.pause_reason is PauseReason.EMPLOYEE_NOT_RUNNABLE


def test_resume_clears_a_previous_auto_pause_reason() -> None:
    task = build_task().auto_pause(reason=PauseReason.CREATOR_PERMISSION_REVOKED, now=NOW)

    resumed = task.resume(now=NOW)

    assert resumed.pause_reason is None


def test_trigger_plan_is_idle_before_the_next_run_and_for_paused_tasks() -> None:
    task = build_task()

    assert plan_trigger(task, now=datetime(2026, 7, 17, 10, 59, tzinfo=UTC)).outcome is (
        TriggerOutcome.IDLE
    )
    assert plan_trigger(task.pause(now=NOW), now=NOW + timedelta(days=1)).outcome is (
        TriggerOutcome.IDLE
    )


def test_trigger_on_time_dispatches_that_point_and_advances_to_the_next_one() -> None:
    task = build_task()

    plan = plan_trigger(task, now=datetime(2026, 7, 17, 11, 0, 5, tzinfo=UTC))

    assert plan.outcome is TriggerOutcome.DISPATCH
    assert plan.scheduled_for == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert plan.next_run_at == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def test_a_trigger_inside_the_misfire_grace_window_still_counts_as_on_time() -> None:
    task = build_task()
    barely_late = (
        datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
        + timedelta(seconds=DEFAULT_MISFIRE_GRACE_SECONDS)
    )

    plan = plan_trigger(task, now=barely_late)

    assert plan.outcome is TriggerOutcome.DISPATCH
    assert plan.scheduled_for == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


def test_misfire_skip_records_one_skip_and_jumps_to_the_next_future_point() -> None:
    task = build_task(misfire_policy=MisfirePolicy.SKIP)

    # 进程停机 3 小时后恢复：11:00/12:00/13:00 三个触发点全部错过。
    plan = plan_trigger(task, now=datetime(2026, 7, 17, 13, 30, tzinfo=UTC))

    assert plan.outcome is TriggerOutcome.SKIP
    assert plan.skip_reason is SkipReason.MISFIRE_SKIPPED
    assert plan.scheduled_for == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert plan.next_run_at == datetime(2026, 7, 17, 14, 0, tzinfo=UTC)


def test_misfire_run_once_backfills_a_single_run_and_jumps_to_the_next_future_point() -> None:
    task = build_task(misfire_policy=MisfirePolicy.RUN_ONCE)

    plan = plan_trigger(task, now=datetime(2026, 7, 17, 13, 30, tzinfo=UTC))

    assert plan.outcome is TriggerOutcome.DISPATCH
    assert plan.scheduled_for == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert plan.next_run_at == datetime(2026, 7, 17, 14, 0, tzinfo=UTC)


def test_misfire_run_all_backfills_the_missed_points_one_per_tick() -> None:
    task = build_task(misfire_policy=MisfirePolicy.RUN_ALL)
    now = datetime(2026, 7, 17, 13, 30, tzinfo=UTC)

    first = plan_trigger(task, now=now)
    assert first.outcome is TriggerOutcome.DISPATCH
    assert first.scheduled_for == datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    assert first.next_run_at == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)

    second = plan_trigger(task.advance_to(first.next_run_at, now=now), now=now)
    assert second.scheduled_for == datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    assert second.next_run_at == datetime(2026, 7, 17, 13, 0, tzinfo=UTC)


def test_misfire_run_all_drops_points_older_than_the_backfill_window() -> None:
    task = build_task(misfire_policy=MisfirePolicy.RUN_ALL)

    # 停机一整年：超出补跑窗口的触发点必须一次性丢弃，不能补跑上万个 Run。
    now = datetime(2027, 7, 17, 10, 30, tzinfo=UTC)
    plan = plan_trigger(task, now=now)

    assert plan.outcome is TriggerOutcome.SKIP
    assert plan.skip_reason is SkipReason.MISFIRE_WINDOW_EXCEEDED
    assert plan.next_run_at is not None
    assert plan.next_run_at >= now - timedelta(seconds=task.misfire_backfill_window_seconds)


def test_a_missed_one_shot_task_is_exhausted_after_its_misfire_decision() -> None:
    run_at = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)
    for policy, outcome in (
        (MisfirePolicy.SKIP, TriggerOutcome.SKIP),
        (MisfirePolicy.RUN_ONCE, TriggerOutcome.DISPATCH),
        (MisfirePolicy.RUN_ALL, TriggerOutcome.DISPATCH),
    ):
        task = build_task(
            schedule=Schedule.once(run_at=run_at, timezone="UTC"),
            misfire_policy=policy,
        )
        plan = plan_trigger(task, now=datetime(2026, 7, 17, 13, 30, tzinfo=UTC))

        assert plan.outcome is outcome
        assert plan.scheduled_for == run_at
        assert plan.next_run_at is None
        assert task.advance_to(plan.next_run_at, now=NOW).is_exhausted is True
