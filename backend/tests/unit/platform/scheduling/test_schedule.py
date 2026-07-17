"""C12 调度表达式与时区语义（Cron / 单次预约 / IANA 时区 / DST 边界）。"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from agent_platform.platform.scheduling.errors import (
    InvalidCronExpression,
    InvalidScheduleTimezone,
    InvalidScheduleWindow,
)
from agent_platform.platform.scheduling.schedule import Schedule, ScheduleKind

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


def test_cron_next_occurrence_is_interpreted_in_the_task_timezone() -> None:
    schedule = Schedule.cron(expression="30 9 * * *", timezone="Asia/Shanghai")

    moment = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)

    # 上海 09:30 == UTC 01:30（UTC+8，无夏令时）。
    assert schedule.next_occurrence_after(moment) == datetime(2026, 7, 17, 1, 30, tzinfo=UTC)


def test_cron_next_occurrence_is_strictly_after_the_given_moment() -> None:
    schedule = Schedule.cron(expression="0 * * * *", timezone="UTC")

    exact_hit = datetime(2026, 7, 17, 5, 0, tzinfo=UTC)

    assert schedule.next_occurrence_after(exact_hit) == datetime(2026, 7, 17, 6, 0, tzinfo=UTC)


def test_cron_returns_utc_instants_across_a_daylight_saving_transition() -> None:
    schedule = Schedule.cron(expression="30 9 * * *", timezone="America/New_York")

    before = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
    first = schedule.next_occurrence_after(before)
    second = schedule.next_occurrence_after(first)

    # 3/7 仍是 EST（UTC-5）→ 14:30Z；3/8 起是 EDT（UTC-4）→ 13:30Z。
    assert first == datetime(2026, 3, 7, 14, 30, tzinfo=UTC)
    assert second == datetime(2026, 3, 8, 13, 30, tzinfo=UTC)


def test_cron_spring_forward_skipped_local_time_runs_at_the_next_valid_local_time() -> None:
    # 2026-03-08 美东本地 02:30 不存在（02:00 直接跳到 03:00）。
    schedule = Schedule.cron(expression="30 2 * * *", timezone="America/New_York")

    moment = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    occurrence = schedule.next_occurrence_after(moment)

    assert occurrence.astimezone(NEW_YORK) == datetime(2026, 3, 8, 3, 0, tzinfo=NEW_YORK)


def test_cron_fall_back_repeated_local_time_fires_exactly_once_on_the_first_pass() -> None:
    # 2026-11-01 美东本地 01:30 出现两次（EDT 一次、EST 一次），只能触发一次。
    schedule = Schedule.cron(expression="30 1 * * *", timezone="America/New_York")

    moment = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    first = schedule.next_occurrence_after(moment)
    second = schedule.next_occurrence_after(first)

    # 第一次（EDT，UTC-4）= 05:30Z；第二次（EST，UTC-5）= 06:30Z 必须被跳过。
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_cron_rejects_invalid_expressions() -> None:
    for expression in ["bogus", "* * * *", "60 * * * *", "0 0 30 2 *"]:
        with pytest.raises(InvalidCronExpression):
            Schedule.cron(expression=expression, timezone="UTC")


def test_cron_rejects_unknown_timezone() -> None:
    with pytest.raises(InvalidScheduleTimezone):
        Schedule.cron(expression="0 * * * *", timezone="Mars/Olympus_Mons")


def test_cron_rejects_fixed_offset_and_empty_timezone() -> None:
    for timezone in ["", "+08:00"]:
        with pytest.raises(InvalidScheduleTimezone):
            Schedule.cron(expression="0 * * * *", timezone=timezone)


def test_one_shot_schedule_yields_its_instant_once_and_then_stops() -> None:
    run_at = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    schedule = Schedule.once(run_at=run_at, timezone="Asia/Shanghai")

    assert schedule.kind is ScheduleKind.ONCE
    assert schedule.next_occurrence_after(datetime(2026, 7, 17, 0, 0, tzinfo=UTC)) == run_at
    assert schedule.next_occurrence_after(run_at) is None


def test_one_shot_schedule_normalizes_aware_instants_to_utc() -> None:
    schedule = Schedule.once(
        run_at=datetime(2026, 7, 18, 11, 0, tzinfo=SHANGHAI), timezone="Asia/Shanghai"
    )

    assert schedule.run_at == datetime(2026, 7, 18, 3, 0, tzinfo=UTC)


def test_one_shot_schedule_rejects_naive_instants() -> None:
    with pytest.raises(InvalidScheduleWindow):
        Schedule.once(run_at=datetime(2026, 7, 18, 3, 0), timezone="UTC")


def test_schedule_round_trips_through_its_stored_representation() -> None:
    cron_schedule = Schedule.cron(expression="30 9 * * 1-5", timezone="Asia/Shanghai")
    once_schedule = Schedule.once(
        run_at=datetime(2026, 7, 18, 3, 0, tzinfo=UTC), timezone="UTC"
    )

    for schedule in (cron_schedule, once_schedule):
        assert (
            Schedule.restore(
                kind=schedule.kind,
                timezone=schedule.timezone,
                cron_expression=schedule.cron_expression,
                run_at=schedule.run_at,
            )
            == schedule
        )
