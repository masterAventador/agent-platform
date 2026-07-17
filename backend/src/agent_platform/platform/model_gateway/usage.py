"""模型用量记录领域（C16 阶段二，纯观测面：只记录、不干预）。

记录每次**物理**模型调用的：provider-neutral alias、prompt/completion/total token、
延迟、结果（success/error）、错误分类、按平台定价表计算的费用、以及任务归属
（tenant/run/employee）。归属可为 None（未知），但绝不因归属缺失而丢弃整条记录。

金额只用整数 nano-USD 或 None（未知），绝不浮点。model_alias 只记 provider-neutral
alias，绝不记 LiteLLM 返回的真实模型名（防供应商泄露 / 平台协议不泄露内部结构）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ModelCallOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ModelUsageRecord:
    id: UUID
    tenant_id: UUID
    run_id: UUID | None
    employee_id: UUID | None
    model_alias: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    outcome: ModelCallOutcome
    error_type: str | None
    cost_nanousd: int | None
    cost_source: str | None
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.latency_ms, int) or isinstance(self.latency_ms, bool):
            raise TypeError("latency_ms must be an int")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.cost_nanousd is not None and (
            not isinstance(self.cost_nanousd, int) or isinstance(self.cost_nanousd, bool)
        ):
            raise TypeError("cost_nanousd must be an int or None (no float money)")
        if self.cost_nanousd is not None and self.cost_nanousd < 0:
            raise ValueError("cost_nanousd must be non-negative")
        for name, value in (
            ("prompt_tokens", self.prompt_tokens),
            ("completion_tokens", self.completion_tokens),
            ("total_tokens", self.total_tokens),
        ):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise TypeError(f"{name} must be an int or None")
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.outcome is ModelCallOutcome.ERROR and self.error_type is None:
            raise ValueError("error outcome requires an error_type classification")
        if self.outcome is ModelCallOutcome.SUCCESS and self.error_type is not None:
            raise ValueError("success outcome must not carry an error_type")
        # 费用与来源必须同时可知或同时未知：分开会让阶段三读到「有费用无来源」这种脏态。
        if (self.cost_nanousd is None) != (self.cost_source is None):
            raise ValueError("cost_nanousd and cost_source must both be set or both be unset")


class ModelUsageRecorder(Protocol):
    """把一条用量记录持久化；实现必须在失败时不拖垮主链路（详见捕获层）。"""

    async def record(self, record: ModelUsageRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class ModelUsageCursor:
    """keyset 分页游标：按 (recorded_at, id) 降序稳定翻页。"""

    recorded_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class ModelUsageQuery:
    tenant_id: UUID
    start: datetime | None = None
    end: datetime | None = None
    limit: int = 50
    cursor: ModelUsageCursor | None = None


@dataclass(frozen=True, slots=True)
class ModelUsagePage:
    records: tuple[ModelUsageRecord, ...]
    next_cursor: ModelUsageCursor | None
