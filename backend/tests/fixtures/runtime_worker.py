from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_platform.config import AppSettings
from agent_platform.workers.main import run_worker_service
from agent_platform.workers.runtime_composition import PlatformModelResolver

RUNTIME_E2E_OUTPUT = "Runtime E2E completed in the real worker."
RUNTIME_E2E_WORKER_READY_FILE = Path("/tmp/agent-platform-runtime-e2e-worker-ready")


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


def _messages() -> Iterator[AIMessage | str]:
    while True:
        yield AIMessage(content=RUNTIME_E2E_OUTPUT)


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
    model = ToolBindingGenericFakeChatModel(messages=_messages())
    resolver = PlatformModelResolver(
        injected_models={("openai", "gpt-5"): model},
    )
    await run_worker_service(
        settings=settings,
        model_resolver=resolver,
        ready_file=RUNTIME_E2E_WORKER_READY_FILE,
    )


if __name__ == "__main__":
    asyncio.run(_main())
