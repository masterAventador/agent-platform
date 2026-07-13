import asyncio

import pytest
from pydantic import ValidationError

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnershipBusy,
)
from agent_platform.runtimes.recovery import RuntimeRecoveryTransient
from agent_platform.workers import main as worker_main_module
from agent_platform.workers.main import (
    WorkerConfigurationError,
    WorkerHealth,
    main,
    run_worker_service,
    serve,
    wait_for_runtime_recovery,
)
from agent_platform.workers.run_worker import WorkerFenced


class RecordingWorker:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.calls = 0
        self.close_calls = 0
        self.renew_calls = 0

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        del block_ms
        self.calls += 1
        if self.calls == 2:
            self.stop_event.set()
        return self.calls == 1

    async def renew_active_runtimes(self) -> None:
        self.renew_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1


class FailingOnceWorker:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.calls = 0
        self.close_calls = 0

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        del block_ms
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("redis-password-must-not-be-logged")
        self.stop_event.set()
        return True

    async def renew_active_runtimes(self) -> None:
        return None

    async def aclose(self) -> None:
        self.close_calls += 1


class HeartbeatStoppingWorker:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.renew_calls = 0
        self.close_calls = 0

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        del block_ms
        await self.stop_event.wait()
        return False

    async def renew_active_runtimes(self) -> None:
        self.renew_calls += 1
        self.stop_event.set()

    async def aclose(self) -> None:
        self.close_calls += 1


class FencedWorker:
    def __init__(self) -> None:
        self.calls = 0
        self.close_calls = 0

    async def run_once(self, *, block_ms: int = 5_000) -> bool:
        del block_ms
        self.calls += 1
        raise WorkerFenced

    async def renew_active_runtimes(self) -> None:
        return None

    async def aclose(self) -> None:
        self.close_calls += 1


class BusyRecoveryWorker:
    def __init__(self) -> None:
        self.calls = 0

    async def recover_incomplete_runs(self) -> int:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeOwnershipBusy
        return 1


class TransientRecoveryWorker(BusyRecoveryWorker):
    async def recover_incomplete_runs(self) -> int:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeRecoveryTransient
        return 1


@pytest.mark.asyncio
async def test_serve_reports_ready_and_stops_after_current_dequeue() -> None:
    stop_event = asyncio.Event()
    worker = RecordingWorker(stop_event)
    health = WorkerHealth()

    await serve(worker=worker, stop_event=stop_event, health=health, block_ms=1)

    assert worker.calls == 2
    assert worker.close_calls == 1
    assert health.live is False
    assert health.ready is False
    assert health.single_replica is True


@pytest.mark.asyncio
async def test_serve_logs_a_sanitized_failure_and_continues_processing(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        worker_main_module.logger,
        "error",
        lambda message, *, extra: logged.append((message, extra)),
    )
    stop_event = asyncio.Event()
    worker = FailingOnceWorker(stop_event)
    health = WorkerHealth()

    await serve(
        worker=worker,
        stop_event=stop_event,
        health=health,
        block_ms=1,
        retry_backoff_seconds=0,
    )

    assert worker.calls == 2
    assert worker.close_calls == 1
    assert logged == [("worker_delivery_processing_failed", {"error_type": "RuntimeError"})]
    assert "redis-password-must-not-be-logged" not in repr(logged)
    assert health.live is False
    assert health.ready is False


@pytest.mark.asyncio
async def test_serve_renews_active_sandboxes_before_ttl_and_closes_on_stop() -> None:
    stop_event = asyncio.Event()
    worker = HeartbeatStoppingWorker(stop_event)

    await serve(
        worker=worker,
        stop_event=stop_event,
        health=WorkerHealth(),
        heartbeat_interval_seconds=0.01,
    )

    assert worker.renew_calls == 1
    assert worker.close_calls == 1


@pytest.mark.asyncio
async def test_serve_stops_immediately_when_runtime_ownership_is_fenced() -> None:
    stop_event = asyncio.Event()
    worker = FencedWorker()
    health = WorkerHealth()

    await serve(
        worker=worker,
        stop_event=stop_event,
        health=health,
        retry_backoff_seconds=0,
    )

    assert stop_event.is_set()
    assert worker.calls == 1
    assert worker.close_calls == 1
    assert health.ready is False


@pytest.mark.asyncio
async def test_startup_stays_not_ready_until_previous_owner_lease_expires() -> None:
    worker = BusyRecoveryWorker()
    stop_event = asyncio.Event()

    recovered = await wait_for_runtime_recovery(
        worker=worker,
        stop_event=stop_event,
        retry_seconds=0.001,
    )

    assert recovered == 1
    assert worker.calls == 2


@pytest.mark.asyncio
async def test_startup_recovery_wait_is_cancellable() -> None:
    worker = BusyRecoveryWorker()
    stop_event = asyncio.Event()
    stop_event.set()

    recovered = await wait_for_runtime_recovery(
        worker=worker,
        stop_event=stop_event,
        retry_seconds=0.001,
    )

    assert recovered is None
    assert worker.calls == 0


@pytest.mark.asyncio
async def test_startup_stays_not_ready_and_retries_sanitized_transient_recovery() -> None:
    worker = TransientRecoveryWorker()

    recovered = await wait_for_runtime_recovery(
        worker=worker,
        stop_event=asyncio.Event(),
        retry_seconds=0.001,
    )

    assert recovered == 1
    assert worker.calls == 2


@pytest.mark.asyncio
async def test_service_fails_fast_when_builtin_sandbox_is_not_configured() -> None:
    with pytest.raises(WorkerConfigurationError, match="builtin runtime adapters"):
        await run_worker_service(runtime_resolver=None)


@pytest.mark.asyncio
async def test_service_rejects_multiple_replicas_until_runtime_registry_is_durable() -> None:
    with pytest.raises(WorkerConfigurationError, match="single replica"):
        await run_worker_service(runtime_resolver=object(), replicas=2)


@pytest.mark.parametrize(
    "overrides",
    [
        {"queue_pending_min_idle_ms": 0},
        {"queue_max_delivery_attempts": 0},
        {"queue_max_delivery_attempts": 101},
        {"worker_retry_backoff_seconds": -0.1},
        {"worker_retry_backoff_seconds": 60.1},
        {"sandbox_controller_request_timeout_seconds": 124},
        {"runtime_lease_seconds": 0},
        {"runtime_heartbeat_seconds": 30, "runtime_lease_seconds": 30},
        {"runtime_heartbeat_seconds": 31, "runtime_lease_seconds": 30},
        {"runtime_cancel_poll_initial_seconds": 0},
        {
            "runtime_cancel_poll_initial_seconds": 2,
            "runtime_cancel_poll_max_seconds": 1,
        },
    ],
)
def test_worker_retry_settings_fail_fast_outside_safe_bounds(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**overrides)


def test_worker_retry_settings_have_bounded_dlq_defaults() -> None:
    settings = AppSettings()

    assert settings.queue_max_delivery_attempts == 5
    assert settings.run_queue_dead_letter_stream_name == "agent-platform:runs:dlq"
    assert settings.runtime_lease_seconds == 30
    assert settings.runtime_heartbeat_seconds == 10
    assert settings.runtime_cancel_poll_initial_seconds == 0.25
    assert settings.runtime_cancel_poll_max_seconds == 2


def test_cli_missing_adapter_exits_two_with_sanitized_stderr(monkeypatch, capsys) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_SANDBOX_CONTROLLER_SECRET", raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_WORKER_REPLICAS", raising=False)

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert stderr.startswith("worker configuration error:")
    assert "postgresql" not in stderr
    assert "redis://" not in stderr
    assert "agent-platform-local" not in stderr
