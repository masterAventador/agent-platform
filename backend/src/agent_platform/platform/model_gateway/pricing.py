"""平台自持的 provider-neutral alias 定价表（C16 阶段二，纯观测面）。

**为什么定价在平台侧而不取自 LiteLLM**：C16 阶段一实测（真实 LiteLLM v1.86.2）确认，
LiteLLM 只把费用放在 HTTP 响应头 ``x-litellm-response-cost-original``，**不放进响应体**；
而平台走标准 ``ChatOpenAI`` + LangChain 回调捕获，回调只能拿到 token/usage_metadata，
拿不到任何响应头。要取那个头必须绕过 LangChain 公开模型（自己重写 openai 调用或挂
httpx 事件钩子），属侵入。因此费用由平台按 alias 声明价 × token 数计算。

**金额绝不用浮点**：价格以「nano-USD / 每百万 token」的整数表达，费用以 nano-USD 整数计算。
alias 无定价或 token 未知时费用是「未知」（None），绝不塌缩成 0——阶段三预算会读它，
0 与「未知」语义不同。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# 费用来源标签：写进用量记录，让阶段三/前端能区分「按平台定价表计算」与其它来源。
COST_SOURCE_PRICING_TABLE = "platform_pricing_table"
_NANOUSD_PER_MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelAliasPrice:
    """某 provider-neutral alias 的平台声明价，单位 nano-USD / 每百万 token。"""

    input_nanousd_per_million: int
    output_nanousd_per_million: int

    def __post_init__(self) -> None:
        if not isinstance(self.input_nanousd_per_million, int) or isinstance(
            self.input_nanousd_per_million, bool
        ):
            raise TypeError("input_nanousd_per_million must be an int (no float money)")
        if not isinstance(self.output_nanousd_per_million, int) or isinstance(
            self.output_nanousd_per_million, bool
        ):
            raise TypeError("output_nanousd_per_million must be an int (no float money)")
        if self.input_nanousd_per_million < 0 or self.output_nanousd_per_million < 0:
            raise ValueError("alias price must be non-negative")


class ModelPricingTable:
    """按 alias 计算费用；未知 alias / 未知 token 返回 None（未知），不返回 0。"""

    def __init__(self, prices: Mapping[str, ModelAliasPrice]) -> None:
        self._prices = dict(prices)

    def cost_nanousd(
        self,
        *,
        alias: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> int | None:
        price = self._prices.get(alias)
        if price is None or prompt_tokens is None or completion_tokens is None:
            return None
        total = (
            prompt_tokens * price.input_nanousd_per_million
            + completion_tokens * price.output_nanousd_per_million
        )
        # 就近取整到 nano-USD 整数（全整数运算，绝不引入浮点）。
        return (total + _NANOUSD_PER_MILLION // 2) // _NANOUSD_PER_MILLION

    @classmethod
    def from_config(cls, raw: Mapping[str, Mapping[str, object]]) -> ModelPricingTable:
        prices: dict[str, ModelAliasPrice] = {}
        for alias, spec in raw.items():
            prices[alias] = ModelAliasPrice(
                input_nanousd_per_million=_require_int(spec["input_nanousd_per_million"]),
                output_nanousd_per_million=_require_int(spec["output_nanousd_per_million"]),
            )
        return cls(prices)


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("price fields must be integers (no float money)")
    return value


# 默认占位定价（可经配置覆盖）：为演示/开发让费用管线可端到端验证。
# **这是平台声明的占位价，不是真实供应商账单**——alias 背后的真实模型由 LiteLLM 路由，
# 平台不感知；运维接入真实供应商后必须按实际单价调整。数量级参考通用中端 LLM。
DEFAULT_MODEL_PRICING = ModelPricingTable(
    {
        "general-purpose": ModelAliasPrice(
            input_nanousd_per_million=400_000_000,  # $0.40 / 1M input tokens（占位）
            output_nanousd_per_million=1_200_000_000,  # $1.20 / 1M output tokens（占位）
        )
    }
)
