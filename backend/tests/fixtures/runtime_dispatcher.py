from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.workers.dispatcher_main import DispatcherHealth, run_dispatcher_service

load_database_models()


RUNTIME_E2E_DISPATCHER_READY_FILE = Path(
    os.getenv(
        "RUNTIME_E2E_DISPATCHER_READY_FILE",
        "/tmp/agent-platform-runtime-e2e-dispatcher-ready",
    )
)


async def _wait_for_runtime_database(settings: AppSettings) -> None:
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
    await run_dispatcher_service(
        settings=settings,
        health=DispatcherHealth(ready_file=RUNTIME_E2E_DISPATCHER_READY_FILE),
    )


if __name__ == "__main__":
    asyncio.run(_main())
