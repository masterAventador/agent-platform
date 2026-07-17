"""平台侧 provider-neutral alias 定价表（C16 阶段二）。

费用绝不用浮点存储：价格以 nano-USD / 每百万 token 的整数表达，费用以 nano-USD 整数计算。
alias 无定价或 token 未知时费用必须是「未知」（None），绝不塌缩成 0——0 与未知语义不同。
"""

from __future__ import annotations

import pytest

from agent_platform.platform.model_gateway.pricing import (
    DEFAULT_MODEL_PRICING,
    ModelAliasPrice,
    ModelPricingTable,
)


def test_cost_is_integer_nanousd_rounded_from_tokens_and_declared_price() -> None:
    table = ModelPricingTable(
        {
            "general-purpose": ModelAliasPrice(
                input_nanousd_per_million=400_000_000,
                output_nanousd_per_million=1_200_000_000,
            )
        }
    )
    # 1000 input @ $0.4/1M + 500 output @ $1.2/1M
    #  = 1000*400_000_000/1_000_000 + 500*1_200_000_000/1_000_000
    #  = 400_000 + 600_000 = 1_000_000 nano-USD ($0.001)
    cost = table.cost_nanousd(
        alias="general-purpose", prompt_tokens=1000, completion_tokens=500
    )
    assert cost == 1_000_000
    assert isinstance(cost, int)


def test_unknown_alias_returns_none_not_zero() -> None:
    table = ModelPricingTable({})
    assert (
        table.cost_nanousd(
            alias="general-purpose", prompt_tokens=10, completion_tokens=5
        )
        is None
    )


def test_unknown_tokens_returns_none_not_zero() -> None:
    table = ModelPricingTable(
        {
            "general-purpose": ModelAliasPrice(
                input_nanousd_per_million=1,
                output_nanousd_per_million=1,
            )
        }
    )
    assert (
        table.cost_nanousd(
            alias="general-purpose", prompt_tokens=None, completion_tokens=5
        )
        is None
    )
    assert (
        table.cost_nanousd(
            alias="general-purpose", prompt_tokens=5, completion_tokens=None
        )
        is None
    )


def test_zero_tokens_yields_zero_cost_when_price_is_known() -> None:
    # 已知定价 + 明确 0 token → 费用确实是 0（区别于未知）。
    table = ModelPricingTable(
        {
            "general-purpose": ModelAliasPrice(
                input_nanousd_per_million=1,
                output_nanousd_per_million=1,
            )
        }
    )
    assert (
        table.cost_nanousd(
            alias="general-purpose", prompt_tokens=0, completion_tokens=0
        )
        == 0
    )


def test_price_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        ModelAliasPrice(input_nanousd_per_million=-1, output_nanousd_per_million=0)
    with pytest.raises(ValueError):
        ModelAliasPrice(input_nanousd_per_million=0, output_nanousd_per_million=-5)


def test_from_config_mapping_parses_integer_prices() -> None:
    table = ModelPricingTable.from_config(
        {"general-purpose": {"input_nanousd_per_million": 2, "output_nanousd_per_million": 3}}
    )
    assert table.cost_nanousd(
        alias="general-purpose", prompt_tokens=1_000_000, completion_tokens=1_000_000
    ) == 5


def test_from_config_rejects_non_integer_price_to_avoid_float_money() -> None:
    with pytest.raises((TypeError, ValueError)):
        ModelPricingTable.from_config(
            {"general-purpose": {"input_nanousd_per_million": 1.5, "output_nanousd_per_million": 2}}
        )


def test_default_pricing_table_prices_general_purpose_alias() -> None:
    # 默认表为演示/开发提供 general-purpose 的可调占位价，使费用管线可端到端验证。
    assert isinstance(DEFAULT_MODEL_PRICING, ModelPricingTable)
    cost = DEFAULT_MODEL_PRICING.cost_nanousd(
        alias="general-purpose", prompt_tokens=1000, completion_tokens=1000
    )
    assert cost is not None and cost > 0
