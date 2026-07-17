"""C12 调度表达式：Cron / 单次预约与 IANA 时区语义。

Cron 表达式解析与 DST 语义委托给 cronsim（零依赖、公开 API）：
- 春季跳过的本地时间（如美东 3/8 02:30 不存在）→ 在该日的下一个有效本地时间触发；
- 秋季重复的本地时间（如美东 11/1 01:30 出现两次）→ 只在第一次（DST 侧）触发。
本模块是全平台唯一直接依赖 cronsim 的位置；对外只暴露 UTC 瞬时。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

from agent_platform.platform.scheduling.errors import (
    InvalidCronExpression,
    InvalidScheduleTimezone,
    InvalidScheduleWindow,
)

CRON_EXPRESSION_MAX_LENGTH = 200
TIMEZONE_MAX_LENGTH = 64


class ScheduleKind(StrEnum):
    CRON = "cron"
    ONCE = "once"


def resolve_timezone(timezone: str) -> ZoneInfo:
    """把 IANA 时区名解析为 ZoneInfo；固定偏移与空值一律拒绝。

    只接受 IANA 名字（而非 `+08:00` 之类固定偏移），因为 Cron 必须按当地
    民用时间解释，固定偏移无法表达 DST。
    """

    if not timezone or len(timezone) > TIMEZONE_MAX_LENGTH:
        raise InvalidScheduleTimezone(timezone)
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise InvalidScheduleTimezone(timezone) from error


@dataclass(frozen=True, slots=True)
class Schedule:
    kind: ScheduleKind
    timezone: str
    cron_expression: str | None = None
    run_at: datetime | None = None

    @classmethod
    def cron(cls, *, expression: str, timezone: str) -> Schedule:
        zone = resolve_timezone(timezone)
        normalized = expression.strip()
        if not normalized or len(normalized) > CRON_EXPRESSION_MAX_LENGTH:
            raise InvalidCronExpression(expression)
        try:
            # 解析期即校验：非法字段与永不触发的表达式（如 2/30）在此被拒。
            CronSim(normalized, datetime.now(UTC).astimezone(zone))
        except CronSimError as error:
            raise InvalidCronExpression(str(error)) from error
        return cls(kind=ScheduleKind.CRON, timezone=timezone, cron_expression=normalized)

    @classmethod
    def once(cls, *, run_at: datetime, timezone: str) -> Schedule:
        resolve_timezone(timezone)
        if run_at.tzinfo is None:
            raise InvalidScheduleWindow("单次预约时间必须带时区信息")
        return cls(kind=ScheduleKind.ONCE, timezone=timezone, run_at=run_at.astimezone(UTC))

    @classmethod
    def restore(
        cls,
        *,
        kind: ScheduleKind,
        timezone: str,
        cron_expression: str | None,
        run_at: datetime | None,
    ) -> Schedule:
        """从持久化字段重建；不重复校验（写入路径已校验过）。"""

        return cls(
            kind=kind,
            timezone=timezone,
            cron_expression=cron_expression,
            run_at=run_at,
        )

    def next_occurrence_after(self, moment: datetime) -> datetime | None:
        """严格晚于 moment 的下一个触发点（UTC）；不再有触发点时返回 None。"""

        if self.kind is ScheduleKind.ONCE:
            if self.run_at is None or self.run_at <= moment:
                return None
            return self.run_at
        if self.cron_expression is None:
            raise InvalidCronExpression("cron 调度缺少表达式")
        zone = resolve_timezone(self.timezone)
        try:
            occurrence = next(CronSim(self.cron_expression, moment.astimezone(zone)))
        except CronSimError as error:
            raise InvalidCronExpression(str(error)) from error
        return occurrence.astimezone(UTC)
