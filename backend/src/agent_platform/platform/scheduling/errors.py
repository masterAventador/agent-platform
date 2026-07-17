class SchedulingError(Exception):
    """定时任务领域错误基类。"""


class InvalidCronExpression(SchedulingError):
    """Cron 表达式非法或永远不会触发。"""


class InvalidScheduleTimezone(SchedulingError):
    """时区不是可解析的 IANA 时区名。"""


class InvalidScheduleWindow(SchedulingError):
    """单次预约时间非法（缺时区信息或不在允许窗口内）。"""


class InvalidScheduledTaskTransition(SchedulingError):
    """定时任务状态机非法转换。"""


class InvalidScheduledTaskExecutionTransition(SchedulingError):
    """执行记录状态机非法转换。"""
