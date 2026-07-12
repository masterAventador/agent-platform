import asyncio
from types import SimpleNamespace

import pytest

from agent_platform.config import AppSettings
from agent_platform.workers.main import (
    WorkerConfigurationError,
    WorkerHealth,
    _load_runtime_adapters,
    main,
    run_worker_service,
    serve,
)


class RecordingWorker:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.calls = 0

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        del block_ms
        self.calls += 1
        if self.calls == 2:
            self.stop_event.set()
        return self.calls == 1


@pytest.mark.asyncio
async def test_serve_reports_ready_and_stops_after_current_dequeue() -> None:
    stop_event = asyncio.Event()
    worker = RecordingWorker(stop_event)
    health = WorkerHealth()

    await serve(worker=worker, stop_event=stop_event, health=health, block_ms=1)

    assert worker.calls == 2
    assert health.live is False
    assert health.ready is False
    assert health.single_replica is True


@pytest.mark.asyncio
async def test_service_fails_fast_without_concrete_runtime_resolver() -> None:
    with pytest.raises(WorkerConfigurationError, match="runtime resolver"):
        await run_worker_service(runtime_resolver=None)


@pytest.mark.asyncio
async def test_service_rejects_multiple_replicas_until_runtime_registry_is_durable() -> None:
    with pytest.raises(WorkerConfigurationError, match="single replica"):
        await run_worker_service(runtime_resolver=object(), replicas=2)


def test_adapter_factory_result_is_validated_during_startup(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY",
        "test_runtime_adapters:create",
    )
    monkeypatch.setattr(
        "agent_platform.workers.main.importlib.import_module",
        lambda _: SimpleNamespace(create=lambda settings: object()),
    )

    with pytest.raises(WorkerConfigurationError, match="required capabilities"):
        _load_runtime_adapters(AppSettings())


def test_cli_missing_adapter_exits_two_with_sanitized_stderr(monkeypatch, capsys) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY", raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_WORKER_REPLICAS", raising=False)

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert stderr.startswith("worker configuration error:")
    assert "postgresql" not in stderr
    assert "redis://" not in stderr
    assert "agent-platform-local" not in stderr
