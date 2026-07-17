"""模型用量捕获回调（C16 阶段二，纯观测面）。

捕获走 LangChain 公开扩展点 ``AsyncCallbackHandler``（零侵入），覆盖失败矩阵：
- 成功调用记 token/费用/延迟/归属；
- 失败调用（超时/5xx 等）也记一条（error 分类 + 延迟，无 token/费用）；
- 流式：usage 在最后一个 chunk 的 usage_metadata；
- 工具调用循环：同一 run 多次物理调用各记一条、都归属同一 run；
- 落库失败绝不拖垮主链路，但要有可观测信号（不是空 except）；
- 记录的是 provider-neutral alias，绝不是 llm_output 里的真实模型名（协议不泄露）；
- 并发不同 run 的归属不串。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agent_platform.platform.model_gateway.pricing import (
    ModelAliasPrice,
    ModelPricingTable,
)
from agent_platform.platform.model_gateway.usage import (
    ModelCallOutcome,
    ModelUsageRecord,
)
from agent_platform.workers.model_usage_capture import (
    ModelUsageCaptureHandler,
    attach_usage_capture,
)

PRICING = ModelPricingTable(
    {
        "general-purpose": ModelAliasPrice(
            input_nanousd_per_million=1_000_000,
            output_nanousd_per_million=1_000_000,
        )
    }
)


class _CollectingRecorder:
    def __init__(self) -> None:
        self.records: list[ModelUsageRecord] = []

    async def record(self, record: ModelUsageRecord) -> None:
        self.records.append(record)


class _FailingRecorder:
    async def record(self, record: ModelUsageRecord) -> None:
        raise RuntimeError("db down")


def _handler(
    recorder, *, tenant=None, run=None, employee=None, alias="general-purpose", signals=None
):
    clock = iter([0.0, 0.25, 0.0, 0.25, 0.0, 0.25])
    return ModelUsageCaptureHandler(
        recorder=recorder,
        pricing=PRICING,
        tenant_id=tenant or uuid4(),
        run_id=run or uuid4(),
        employee_id=employee or uuid4(),
        model_alias=alias,
        clock=lambda: next(clock),
        now=lambda: datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        on_persist_failure=(signals.append if signals is not None else None),
    )


def _result_with_usage_metadata(input_tokens=10, output_tokens=5, model_name="real-provider-model"):
    msg = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    return LLMResult(
        generations=[[ChatGeneration(message=msg)]],
        llm_output={"model_name": model_name},
    )


@pytest.mark.asyncio
async def test_success_records_tokens_cost_latency_and_attribution() -> None:
    recorder = _CollectingRecorder()
    tenant, run, employee = uuid4(), uuid4(), uuid4()
    handler = _handler(recorder, tenant=tenant, run=run, employee=employee)
    lc_run = uuid4()
    await handler.on_chat_model_start({}, [], run_id=lc_run)
    await handler.on_llm_end(_result_with_usage_metadata(), run_id=lc_run)

    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert rec.outcome is ModelCallOutcome.SUCCESS
    assert rec.tenant_id == tenant and rec.run_id == run and rec.employee_id == employee
    assert rec.prompt_tokens == 10 and rec.completion_tokens == 5 and rec.total_tokens == 15
    # 10*1 + 5*1 (per-1M) = 15 tokens → cost 15 nano-USD（就近取整）
    assert rec.cost_nanousd == 15
    assert rec.cost_source == "platform_pricing_table"
    assert rec.latency_ms == 250  # 0.25s


@pytest.mark.asyncio
async def test_error_call_is_recorded_with_classification_and_latency() -> None:
    recorder = _CollectingRecorder()
    handler = _handler(recorder)
    lc_run = uuid4()
    await handler.on_chat_model_start({}, [], run_id=lc_run)
    await handler.on_llm_error(TimeoutError("upstream 504"), run_id=lc_run)

    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert rec.outcome is ModelCallOutcome.ERROR
    assert rec.error_type == "TimeoutError"
    assert rec.prompt_tokens is None and rec.cost_nanousd is None
    assert rec.latency_ms == 250


@pytest.mark.asyncio
async def test_streaming_usage_from_final_chunk_metadata_with_no_llm_output() -> None:
    recorder = _CollectingRecorder()
    handler = _handler(recorder)
    lc_run = uuid4()
    msg = AIMessage(
        content="streamed",
        usage_metadata={"input_tokens": 3, "output_tokens": 7, "total_tokens": 10},
    )
    result = LLMResult(generations=[[ChatGeneration(message=msg)]], llm_output=None)
    await handler.on_chat_model_start({}, [], run_id=lc_run)
    await handler.on_llm_end(result, run_id=lc_run)
    rec = recorder.records[0]
    assert rec.prompt_tokens == 3 and rec.completion_tokens == 7 and rec.total_tokens == 10


@pytest.mark.asyncio
async def test_tool_loop_records_each_physical_call_to_same_run() -> None:
    recorder = _CollectingRecorder()
    run = uuid4()
    handler = _handler(recorder, run=run)
    for _ in range(3):
        lc_run = uuid4()
        await handler.on_chat_model_start({}, [], run_id=lc_run)
        await handler.on_llm_end(_result_with_usage_metadata(), run_id=lc_run)
    assert len(recorder.records) == 3
    assert all(r.run_id == run for r in recorder.records)


@pytest.mark.asyncio
async def test_persist_failure_does_not_raise_and_emits_signal() -> None:
    signals: list[str] = []
    handler = _handler(_FailingRecorder(), signals=signals)
    lc_run = uuid4()
    await handler.on_chat_model_start({}, [], run_id=lc_run)
    # 绝不抛出（不能拖垮 Run）
    await handler.on_llm_end(_result_with_usage_metadata(), run_id=lc_run)
    # 但要有可观测失败信号（不是空 except）
    assert signals == ["RuntimeError"]


@pytest.mark.asyncio
async def test_records_provider_neutral_alias_not_real_model_name() -> None:
    recorder = _CollectingRecorder()
    handler = _handler(recorder, alias="general-purpose")
    lc_run = uuid4()
    await handler.on_chat_model_start({}, [], run_id=lc_run)
    await handler.on_llm_end(
        _result_with_usage_metadata(model_name="qwen-max-provider-internal"),
        run_id=lc_run,
    )
    assert recorder.records[0].model_alias == "general-purpose"


@pytest.mark.asyncio
async def test_concurrent_runs_do_not_cross_attribution() -> None:
    recorder = _CollectingRecorder()
    run_a, run_b = uuid4(), uuid4()
    handler_a = _handler(recorder, run=run_a)
    handler_b = _handler(recorder, run=run_b)
    lc_a, lc_b = uuid4(), uuid4()

    async def drive(handler, lc_run):
        await handler.on_chat_model_start({}, [], run_id=lc_run)
        await asyncio.sleep(0)
        await handler.on_llm_end(_result_with_usage_metadata(), run_id=lc_run)

    await asyncio.gather(drive(handler_a, lc_a), drive(handler_b, lc_b))
    by_run = {r.run_id for r in recorder.records}
    assert by_run == {run_a, run_b}


@pytest.mark.asyncio
async def test_attach_usage_capture_does_not_mutate_original_and_survives_bind_tools() -> None:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    recorder = _CollectingRecorder()
    handler = _handler(recorder)
    model = ChatOpenAI(
        model="general-purpose",
        base_url="http://127.0.0.1:1/v1",
        api_key=SecretStr("sk-test"),
        max_retries=0,
    )
    attached = attach_usage_capture(model, handler)
    # 原实例不被污染（避免共享注入模型跨 run 泄漏 callback）
    assert model.callbacks is None
    # 副本带上 handler
    assert attached.callbacks is not None and handler in attached.callbacks
    # bind_tools 后 callback 仍随同一实例保留（这正是选实例级 callback 而非包装器的原因）
    bound = attached.bind_tools([])
    assert handler in bound.bound.callbacks
