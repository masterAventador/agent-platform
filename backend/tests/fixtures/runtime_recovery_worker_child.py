from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from agent_platform.config import AppSettings
from agent_platform.workers.main import run_worker_service
from agent_platform.workers.runtime_composition import PlatformModelResolver

OUTPUT = "Recovery E2E completed after the approved tool call."
READY_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-worker-ready")
MODEL_PHASE_FILE = Path("/tmp/agent-platform-runtime-recovery-e2e-model-phase")


class ToolBindingRecoveryModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self


def _messages() -> Iterator[AIMessage | str]:
    if not MODEL_PHASE_FILE.exists():
        MODEL_PHASE_FILE.touch(mode=0o600)
        yield AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "recovery_external",
                    "args": {"value": "execute-once"},
                    "id": "runtime-recovery-external-call",
                    "type": "tool_call",
                }
            ],
        )
    while True:
        yield AIMessage(content=OUTPUT)


async def _main() -> None:
    settings = AppSettings()
    resolver = PlatformModelResolver(
        injected_models={
            "general-purpose": ToolBindingRecoveryModel(messages=_messages())
        }
    )
    await run_worker_service(settings=settings, model_resolver=resolver, ready_file=READY_FILE)


if __name__ == "__main__":
    asyncio.run(_main())
