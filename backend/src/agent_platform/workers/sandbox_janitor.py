from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata
from agent_platform.workers.runtime_adapters import (
    BuiltinRuntimeAdapters,
    RuntimeAdapterConfigurationError,
    create_runtime_adapters,
)

READY_FILE = Path("/tmp/agent-platform-sandbox-janitor-ready")
logger = logging.getLogger(__name__)


class JanitorManager(Protocol):
    async def cleanup_expired(self, *, limit: int = 100) -> list[UUID]: ...


async def serve_janitor(
    *,
    manager: JanitorManager,
    stop_event: asyncio.Event,
    interval_seconds: float,
    batch_size: int,
    ready_file: Path = READY_FILE,
) -> None:
    ready_file.touch(mode=0o600)
    try:
        while not stop_event.is_set():
            try:
                await manager.cleanup_expired(limit=batch_size)
            except Exception as error:
                logger.error(
                    "sandbox_janitor_cleanup_failed",
                    extra={"error_type": type(error).__name__},
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    finally:
        ready_file.unlink(missing_ok=True)


async def run_janitor_service(
    *,
    settings: AppSettings | None = None,
    stop_event: asyncio.Event | None = None,
    replicas: int = 1,
) -> None:
    if replicas != 1:
        raise RuntimeAdapterConfigurationError("sandbox janitor must run as one replica")
    initialize_database_metadata()
    app_settings = settings or AppSettings()
    engine = create_async_engine(app_settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    adapters: BuiltinRuntimeAdapters | None = None
    service_stop = stop_event or asyncio.Event()
    READY_FILE.unlink(missing_ok=True)
    _install_signal_handlers(service_stop)
    try:
        adapters = create_runtime_adapters(
            settings=app_settings,
            session_factory=session_factory,
        )
        await serve_janitor(
            manager=adapters.sandbox_manager,
            stop_event=service_stop,
            interval_seconds=app_settings.sandbox_janitor_interval_seconds,
            batch_size=app_settings.sandbox_janitor_batch_size,
        )
    finally:
        if adapters is not None:
            await adapters.aclose()
        await engine.dispose()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


def main() -> None:
    replicas = int(os.getenv("AGENT_PLATFORM_SANDBOX_JANITOR_REPLICAS", "1"))
    try:
        asyncio.run(run_janitor_service(replicas=replicas))
    except RuntimeAdapterConfigurationError as error:
        print(f"sandbox janitor configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
