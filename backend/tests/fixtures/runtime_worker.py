from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.workers.main import run_worker_service
from agent_platform.workers.runtime_composition import PlatformModelResolver

load_database_models()

RUNTIME_E2E_OUTPUT = "Runtime E2E completed in the real worker."
TOOL_CALL_SUCCESS_OUTPUT = "Tool call completed in the real worker."
TOOL_CALL_DENIED_OUTPUT = "Tool call was denied by the platform."
STRUCTURED_RUNTIME_E2E_OUTPUT = (
    '{"cards":[{"title":"线索 A","score":0.91}],"summary":"已生成结构化卡片"}'
)
RUNTIME_E2E_WORKER_READY_FILE = Path("/tmp/agent-platform-runtime-e2e-worker-ready")
SLOW_MODEL_STARTED_FILE = Path("/tmp/agent-platform-runtime-e2e-slow-model-started")
SLOW_MODEL_STOPPED_FILE = Path("/tmp/agent-platform-runtime-e2e-slow-model-stopped")
SLOW_MODEL_SIDE_EFFECT_FILE = Path("/tmp/agent-platform-runtime-e2e-slow-model-side-effect")


class ToolBindingGenericFakeChatModel(GenericFakeChatModel):
    """仅测试进程导入；通过公开 bind_tools seam 支持 Deep Agents。"""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self


class ToolBindingRuntimeE2EChatModel(BaseChatModel):
    """Test-only model that returns deterministic output based on public messages."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=_output_for_messages(messages),
        ))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=_output_for_messages(messages),
        ))])

    @property
    def _llm_type(self) -> str:
        return "runtime-e2e-conditional-model"


class ToolBindingCancellableSlowChatModel(BaseChatModel):
    """A test-only model whose public async invocation blocks until cancelled."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        raise AssertionError("slow runtime E2E model must use async invocation")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        SLOW_MODEL_STARTED_FILE.touch()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            SLOW_MODEL_STOPPED_FILE.touch()
            raise
        SLOW_MODEL_SIDE_EFFECT_FILE.touch()
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="unexpected output"))]
        )

    @property
    def _llm_type(self) -> str:
        return "runtime-e2e-cancellable-slow-model"


class ToolBindingToolCallChatModel(BaseChatModel):
    """C09 fixture：第一轮发起 search_customers 工具调用，第二轮根据工具结果收敛。"""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(
            generations=[ChatGeneration(message=self._next_message(messages))]
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(
            generations=[ChatGeneration(message=self._next_message(messages))]
        )

    @staticmethod
    def _next_message(messages: Sequence[BaseMessage]) -> AIMessage:
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                content = _message_content_text(message)
                if message.status == "error" or "tool_denied" in content:
                    return AIMessage(content=TOOL_CALL_DENIED_OUTPUT)
                return AIMessage(content=TOOL_CALL_SUCCESS_OUTPUT)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_customers",
                    "args": {"query": "acme"},
                    "id": "runtime-e2e-tool-call-1",
                    "type": "tool_call",
                }
            ],
        )

    @property
    def _llm_type(self) -> str:
        return "runtime-e2e-tool-call-model"


def _messages() -> Iterator[AIMessage | str]:
    while True:
        yield AIMessage(content=RUNTIME_E2E_OUTPUT)


def _structured_messages() -> Iterator[AIMessage | str]:
    while True:
        yield AIMessage(content=STRUCTURED_RUNTIME_E2E_OUTPUT)


def _message_content_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return str(content)


def _output_for_messages(messages: Sequence[BaseMessage]) -> str:
    joined = "\n".join(_message_content_text(message) for message in messages)
    if "短视频投放" in joined or "结构化线索卡片" in joined:
        return STRUCTURED_RUNTIME_E2E_OUTPUT
    return RUNTIME_E2E_OUTPUT


async def _wait_for_runtime_database(settings: AppSettings) -> None:
    """Wait until global setup has recreated and migrated the isolated database.

    Playwright starts web servers before global setup. Waiting here also ensures
    RedisRunQueue.setup runs after global setup flushes the isolated Redis DB.
    """
    engine = create_async_engine(settings.database_url)
    try:
        for _ in range(240):
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1 FROM run_commands LIMIT 1"))
                return
            except Exception:
                await asyncio.sleep(0.5)
        raise RuntimeError("runtime E2E database did not become ready")
    finally:
        await engine.dispose()


async def _main() -> None:
    settings = AppSettings()
    await _wait_for_runtime_database(settings)
    for marker in (
        SLOW_MODEL_STARTED_FILE,
        SLOW_MODEL_STOPPED_FILE,
        SLOW_MODEL_SIDE_EFFECT_FILE,
    ):
        marker.unlink(missing_ok=True)
    model = ToolBindingRuntimeE2EChatModel()
    structured_model = ToolBindingGenericFakeChatModel(messages=_structured_messages())
    slow_model = ToolBindingCancellableSlowChatModel()
    resolver = PlatformModelResolver(
        injected_models={
            "general-purpose": model,
            "structured-output": structured_model,
            "slow-cancel": slow_model,
            "tool-call": ToolBindingToolCallChatModel(),
        },
    )
    await run_worker_service(
        settings=settings,
        model_resolver=resolver,
        ready_file=RUNTIME_E2E_WORKER_READY_FILE,
    )


if __name__ == "__main__":
    asyncio.run(_main())
