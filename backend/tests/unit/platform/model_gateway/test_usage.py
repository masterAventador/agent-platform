"""模型用量记录领域实体（C16 阶段二，纯观测面）。

不变式（变异验证会逐条撬）：
- 金额只能是整数 nano-USD 或 None（未知），绝不浮点；
- error 调用必须带 error_type 分类，success 调用不带；
- 归属（run/employee）可为 None（未知），但不因归属缺失而无法构造记录；
- latency 非负。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_platform.platform.model_gateway.usage import (
    ModelCallOutcome,
    ModelUsageRecord,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _success(**overrides: object) -> ModelUsageRecord:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        run_id=uuid4(),
        employee_id=uuid4(),
        model_alias="general-purpose",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=42,
        outcome=ModelCallOutcome.SUCCESS,
        error_type=None,
        cost_nanousd=1_000,
        cost_source="platform_pricing_table",
        recorded_at=NOW,
    )
    defaults.update(overrides)
    return ModelUsageRecord(**defaults)  # type: ignore[arg-type]


def test_cost_must_be_int_or_none_never_float() -> None:
    with pytest.raises(TypeError):
        _success(cost_nanousd=1.5)
    # None（未知）与整数都合法（未知时来源也必须为 None）
    assert _success(cost_nanousd=None, cost_source=None).cost_nanousd is None
    assert _success(cost_nanousd=0).cost_nanousd == 0


def test_error_outcome_requires_error_type() -> None:
    with pytest.raises(ValueError):
        _success(outcome=ModelCallOutcome.ERROR, error_type=None)
    record = _success(
        outcome=ModelCallOutcome.ERROR,
        error_type="ReadTimeout",
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        cost_nanousd=None,
        cost_source=None,
    )
    assert record.outcome is ModelCallOutcome.ERROR
    assert record.error_type == "ReadTimeout"


def test_success_outcome_rejects_error_type() -> None:
    with pytest.raises(ValueError):
        _success(error_type="ReadTimeout")


def test_missing_attribution_is_allowed_and_record_still_constructs() -> None:
    # 归属缺失时观测宁可标 unknown 也不丢弃整条记录。
    record = _success(run_id=None, employee_id=None)
    assert record.run_id is None
    assert record.employee_id is None
    assert record.tenant_id is not None


def test_negative_latency_rejected() -> None:
    with pytest.raises(ValueError):
        _success(latency_ms=-1)


def test_cost_source_required_when_cost_known_and_absent_when_unknown() -> None:
    with pytest.raises(ValueError):
        _success(cost_nanousd=100, cost_source=None)
    with pytest.raises(ValueError):
        _success(cost_nanousd=None, cost_source="platform_pricing_table")
