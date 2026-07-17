"""模型用量捕获（C16 阶段二，纯观测面：只记录、不干预）。

**零侵入捕获**：走 LangChain 公开扩展点 ``AsyncCallbackHandler`` 的 ``on_llm_end`` /
``on_llm_error``，作为解析出的 ``BaseChatModel`` 的**实例级 local callbacks** 注入
（在 ``ComposedRuntimeResolver`` 里 per-run 装配）。选它而非「包一层代理 BaseChatModel」：
Deep Agents / LangGraph 会对模型调用 ``bind_tools``——包装器返回的 ``RunnableBinding``
会绕过包装层的捕获；而实例级 callbacks 在 ``bind_tools`` 后仍随同一实例触发，覆盖
流式（usage 在最后一个 chunk）、ReAct 工具循环（每次物理调用各触发一次）、以及错误。

**记账粒度 = 每次 LangChain 物理生成一条**。``ChatOpenAI`` 的 ``max_retries`` 由 openai
客户端在单次调用内部重试，对 LangChain 回调不可见，因此一次逻辑调用只落一条记录，不会
因客户端重试而重复计数。

**归属**：回调默认只知道 model+usage；tenant/run/employee 由装配处经闭包注入。

**绝不拖垮主链路**：落库失败只降级为可观测信号（日志 + 可选回调），绝不抛出、绝不改变
Run 结果；``model_alias`` 只用平台闭包里的 provider-neutral alias，绝不用 LiteLLM 回传的
真实模型名（防供应商泄露）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import LLMResult

from agent_platform.platform.model_gateway.pricing import (
    COST_SOURCE_PRICING_TABLE,
    ModelPricingTable,
)
from agent_platform.platform.model_gateway.usage import (
    ModelCallOutcome,
    ModelUsageRecord,
    ModelUsageRecorder,
)

logger = logging.getLogger(__name__)


class ModelUsageCaptureHandler(AsyncCallbackHandler):
    def __init__(
        self,
        *,
        recorder: ModelUsageRecorder,
        pricing: ModelPricingTable,
        tenant_id: UUID,
        run_id: UUID | None,
        employee_id: UUID | None,
        model_alias: str,
        clock: Callable[[], float] = perf_counter,
        now: Callable[[], datetime] | None = None,
        on_persist_failure: Callable[[str], None] | None = None,
    ) -> None:
        self._recorder = recorder
        self._pricing = pricing
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._employee_id = employee_id
        self._alias = model_alias
        self._clock = clock
        self._now = now or _utcnow
        self._on_persist_failure = on_persist_failure
        # 按 LangChain 的 LLM run_id 记开始时刻，支持一个 Run 内的并发物理调用。
        self._starts: dict[UUID, float] = {}

    async def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._starts[run_id] = self._clock()

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._starts[run_id] = self._clock()

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        latency_ms = self._latency_ms(run_id)
        prompt_tokens, completion_tokens, total_tokens = _extract_tokens(response)
        cost = self._pricing.cost_nanousd(
            alias=self._alias,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        await self._persist(
            ModelUsageRecord(
                id=uuid4(),
                tenant_id=self._tenant_id,
                run_id=self._run_id,
                employee_id=self._employee_id,
                model_alias=self._alias,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                outcome=ModelCallOutcome.SUCCESS,
                error_type=None,
                cost_nanousd=cost,
                cost_source=COST_SOURCE_PRICING_TABLE if cost is not None else None,
                recorded_at=self._now(),
            )
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        latency_ms = self._latency_ms(run_id)
        await self._persist(
            ModelUsageRecord(
                id=uuid4(),
                tenant_id=self._tenant_id,
                run_id=self._run_id,
                employee_id=self._employee_id,
                model_alias=self._alias,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                latency_ms=latency_ms,
                outcome=ModelCallOutcome.ERROR,
                # 只记异常类名分类，绝不记异常文本（可能含响应体/凭据/prompt）。
                error_type=type(error).__name__,
                cost_nanousd=None,
                cost_source=None,
                recorded_at=self._now(),
            )
        )

    def _latency_ms(self, run_id: UUID) -> int:
        started = self._starts.pop(run_id, None)
        if started is None:
            return 0
        return max(0, round((self._clock() - started) * 1000))

    async def _persist(self, record: ModelUsageRecord) -> None:
        try:
            await self._recorder.record(record)
        except Exception as error:
            # 观测面绝不拖垮主链路：失败降级为可观测信号，绝不吞成静默、绝不抛出。
            logger.error(
                "model_usage_record_persist_failed",
                extra={
                    "error_type": type(error).__name__,
                    "run_id": str(self._run_id) if self._run_id is not None else None,
                },
            )
            if self._on_persist_failure is not None:
                self._on_persist_failure(type(error).__name__)


def attach_usage_capture(model: BaseChatModel, handler: AsyncCallbackHandler) -> BaseChatModel:
    """把用量捕获 handler 作为**实例级 local callback** 挂到模型的一个副本上。

    用 ``model_copy`` 而非原地改 ``model.callbacks``：注入的测试模型可能跨 run 复用，
    原地改会让 callback 累积、导致重复计数与归属串号。副本浅拷贝共享底层 HTTP client，
    不重建连接；``bind_tools`` 作用在副本上时 callback 随同一实例保留。
    """

    existing = getattr(model, "callbacks", None)
    if isinstance(existing, list):
        merged: list[object] = [*existing, handler]
    elif existing is None:
        merged = [handler]
    else:
        # callbacks 是 BaseCallbackManager 等非列表形态（生产 ChatOpenAI 默认 None，
        # 不会走到这里）：保守只挂 handler，避免误解释未知管理器结构。
        merged = [handler]
    return model.model_copy(update={"callbacks": merged})


def _utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def _extract_tokens(
    response: LLMResult,
) -> tuple[int | None, int | None, int | None]:
    """优先用标准化 ``usage_metadata``（覆盖流式聚合），回退 ``llm_output.token_usage``。"""

    for generation_list in response.generations:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if isinstance(usage, dict):
                prompt = _coerce_int(usage.get("input_tokens"))
                completion = _coerce_int(usage.get("output_tokens"))
                total = _coerce_int(usage.get("total_tokens"))
                if prompt is not None or completion is not None or total is not None:
                    return prompt, completion, total
    llm_output = response.llm_output
    if isinstance(llm_output, dict):
        token_usage = llm_output.get("token_usage")
        if isinstance(token_usage, dict):
            return (
                _coerce_int(token_usage.get("prompt_tokens")),
                _coerce_int(token_usage.get("completion_tokens")),
                _coerce_int(token_usage.get("total_tokens")),
            )
    return None, None, None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
