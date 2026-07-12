import asyncio
import logging
from pathlib import Path

import pytest

from agent_platform.workers.dispatcher_main import (
    DispatcherConfigurationError,
    DispatcherHealth,
    DispatcherStartupError,
    main,
    run_dispatcher_service,
    serve,
)


class RecordingDispatcher:
    def __init__(
        self,
        stop_event: asyncio.Event,
        results: list[int],
        *,
        ready_file: Path | None = None,
    ) -> None:
        self._stop_event = stop_event
        self._results = iter(results)
        self._ready_file = ready_file
        self.calls = 0
        self.limits: list[int] = []

    async def dispatch_pending(self, *, limit: int = 100) -> int:
        self.limits.append(limit)
        self.calls += 1
        if self._ready_file is not None:
            assert self._ready_file.is_file()
        result = next(self._results)
        if self.calls == 3:
            self._stop_event.set()
        return result


@pytest.mark.asyncio
async def test_serve_dispatches_until_stopped_and_clears_health_file(tmp_path: Path) -> None:
    stop_event = asyncio.Event()
    ready_file = tmp_path / "dispatcher-ready"
    dispatcher = RecordingDispatcher(stop_event, [1, 0, 2], ready_file=ready_file)
    health = DispatcherHealth(ready_file=ready_file)

    await serve(
        dispatcher=dispatcher,
        stop_event=stop_event,
        health=health,
        batch_size=25,
        idle_backoff_seconds=0,
    )

    assert dispatcher.calls == 3
    assert dispatcher.limits == [25, 25, 25]
    assert health.live is False
    assert health.ready is False
    assert health.single_replica is True
    assert ready_file.exists() is False


@pytest.mark.asyncio
async def test_serve_waits_for_stop_during_idle_backoff(tmp_path: Path) -> None:
    stop_event = asyncio.Event()
    dispatcher = RecordingDispatcher(stop_event, [0, 0, 0])
    health = DispatcherHealth(ready_file=tmp_path / "ready")

    task = asyncio.create_task(
        serve(
            dispatcher=dispatcher,
            stop_event=stop_event,
            health=health,
            idle_backoff_seconds=60,
        )
    )
    await asyncio.sleep(0)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1)

    assert dispatcher.calls == 1


@pytest.mark.asyncio
async def test_serve_recovers_from_cycle_failure_without_logging_sensitive_message(
    tmp_path: Path,
    caplog,
) -> None:
    dispatcher_logger = logging.getLogger("agent_platform.workers.dispatcher_main")
    dispatcher_logger.disabled = False
    caplog.set_level(logging.ERROR, logger=dispatcher_logger.name)
    stop_event = asyncio.Event()

    class FailingOnceDispatcher:
        def __init__(self) -> None:
            self.calls = 0

        async def dispatch_pending(self, *, limit: int = 100) -> int:
            del limit
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("payload=password-secret")
            stop_event.set()
            return 1

    dispatcher = FailingOnceDispatcher()

    await serve(
        dispatcher=dispatcher,
        stop_event=stop_event,
        health=DispatcherHealth(ready_file=tmp_path / "ready"),
        idle_backoff_seconds=0,
    )

    assert dispatcher.calls == 2
    assert "RuntimeError" in caplog.text
    assert "password-secret" not in caplog.text


@pytest.mark.asyncio
async def test_service_rejects_multiple_dispatcher_replicas() -> None:
    with pytest.raises(DispatcherConfigurationError, match="single replica"):
        await run_dispatcher_service(dispatcher=object(), replicas=2)


def test_cli_invalid_replica_count_exits_two_without_secrets(monkeypatch, capsys) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_DISPATCHER_REPLICAS", "2")
    monkeypatch.setenv(
        "AGENT_PLATFORM_DATABASE_URL",
        "postgresql+asyncpg://private-user:private-password@database/private",
    )

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert stderr.startswith("dispatcher configuration error:")
    assert "private-user" not in stderr
    assert "private-password" not in stderr


@pytest.mark.asyncio
async def test_dependency_verification_failure_closes_owned_clients(monkeypatch) -> None:
    class Engine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class RedisClient:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    engine = Engine()
    redis = RedisClient()
    monkeypatch.setattr(
        "agent_platform.workers.dispatcher_main.create_async_engine",
        lambda _: engine,
    )
    monkeypatch.setattr(
        "agent_platform.workers.dispatcher_main.Redis.from_url",
        lambda *args, **kwargs: redis,
    )

    async def fail_verification(*args) -> None:
        raise RuntimeError("postgresql://user:password@private-host/database")

    monkeypatch.setattr(
        "agent_platform.workers.dispatcher_main._verify_dependencies",
        fail_verification,
    )

    with pytest.raises(DispatcherStartupError, match="RuntimeError"):
        await run_dispatcher_service()

    assert engine.disposed is True
    assert redis.closed is True


def test_cli_startup_failure_is_sanitized(monkeypatch, capsys) -> None:
    async def fail_startup(**kwargs) -> None:
        del kwargs
        raise DispatcherStartupError("dependency verification failed: RuntimeError")

    monkeypatch.setattr(
        "agent_platform.workers.dispatcher_main.run_dispatcher_service",
        fail_startup,
    )
    monkeypatch.setenv(
        "AGENT_PLATFORM_DATABASE_URL",
        "postgresql+asyncpg://private-user:private-password@database/private",
    )

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 1
    stderr = capsys.readouterr().err
    assert stderr == "dispatcher startup error: dependency verification failed: RuntimeError\n"
    assert "private-user" not in stderr
    assert "private-password" not in stderr
