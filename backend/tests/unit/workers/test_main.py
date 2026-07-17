import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.infrastructure.database.repositories.model_gateway import (
    TenantModelGatewayKeyRecord,
    TenantModelGatewayPolicyRecord,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnershipBusy,
)
from agent_platform.platform.model_gateway.credentials import derive_tenant_gateway_key
from agent_platform.platform.runs.entities import Run
from agent_platform.runtimes.recovery import RuntimeRecoveryTransient
from agent_platform.workers import main as worker_main_module
from agent_platform.workers.main import (
    WorkerConfigurationError,
    WorkerHealth,
    _assert_model_gateway_ready,
    _build_runtime_resolver,
    main,
    run_worker_service,
    serve,
    wait_for_runtime_recovery,
)
from agent_platform.workers.run_worker import WorkerFenced
from agent_platform.workers.runtime_composition import (
    ModelGatewayUnavailable,
    PermanentRuntimePreparationError,
    PublishedModel,
    TransientRuntimePreparationError,
)


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


class RecordingGatewayReadiness:
    def __init__(self) -> None:
        self.aliases: frozenset[str] | None = None

    async def assert_ready(self, aliases: frozenset[str]) -> None:
        self.aliases = aliases


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
    logged: list[tuple[str, tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setattr(
        worker_main_module.logger,
        "error",
        lambda message, *args, extra: logged.append((message, args, extra)),
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
    assert logged == [
        (
            "worker_delivery_processing_failed error_type=%s",
            ("RuntimeError",),
            {"error_type": "RuntimeError"},
        )
    ]
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
        await run_worker_service(
            runtime_resolver=None,
            settings=AppSettings(llm_gateway_api_key="internal-test-key"),
            gateway_readiness=RecordingGatewayReadiness(),
        )


@pytest.mark.asyncio
async def test_service_fails_fast_when_model_gateway_is_not_configured() -> None:
    with pytest.raises(WorkerConfigurationError, match="model gateway") as captured:
        await run_worker_service(runtime_resolver=None)

    assert "key" not in str(captured.value)


def test_worker_configuration_error_never_leaks_the_gateway_url() -> None:
    sensitive_url = "https://user:password@litellm.example/v1?token=gateway-url-secret"
    settings = AppSettings(
        llm_gateway_url=sensitive_url,
        llm_gateway_api_key="internal-gateway-key",
    )

    with pytest.raises(WorkerConfigurationError) as captured:
        _build_runtime_resolver(
            settings=settings,
            session_factory=object(),  # type: ignore[arg-type]
        )

    rendered_errors = []
    error: BaseException | None = captured.value
    while error is not None:
        rendered_errors.append(f"{error!r}\n{error}")
        error = error.__cause__
    rendered_error = "\n".join(rendered_errors)
    assert "password" not in rendered_error
    assert "gateway-url-secret" not in rendered_error


def _seed_active_gateway_state(session, *, tenant_id, user_id) -> None:
    now = datetime.now(UTC)
    session.add(
        TenantModelGatewayPolicyRecord(
            tenant_id=tenant_id,
            enabled=True,
            allowed_aliases=["general-purpose"],
            budget_microusd=1_000_000,
            budget_period="monthly",
            rpm_limit=60,
            tpm_limit=100_000,
            max_parallel_requests=4,
            revision=1,
            status="active",
            created_at=now,
            updated_at=now,
            updated_by=user_id,
        )
    )
    session.add(
        TenantModelGatewayKeyRecord(
            tenant_id=tenant_id,
            key_version=1,
            retired_key_version=None,
            # 模拟 Controller 已完成一次真实对账：网关侧 v1 存在且可用
            provisioned_key_version=1,
            created_at=now,
            updated_at=now,
        )
    )


@pytest.mark.asyncio
async def test_production_worker_assembly_wires_knowledge_runtime_for_bound_employees() -> None:
    """生产装配路径必须注入知识 Provider：RAGFlow 不可达时是瞬态失败，而非永久定义错误。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = AppSettings(
        llm_gateway_api_key="internal-test-key",
        sandbox_controller_secret="sandbox-secret-16chars",
        ragflow_url="http://127.0.0.1:9",
        ragflow_api_key="test-ragflow-key",
    )
    resolver = _build_runtime_resolver(settings=settings, session_factory=session_factory)
    knowledge_base_id = uuid4()
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"question": "年假有几天"},
    )
    async with session_factory() as session:
        session.add(
            KnowledgeBaseRecord(
                id=knowledge_base_id,
                tenant_id=run.tenant_id,
                name="制度库",
                description="员工制度",
                provider="ragflow",
                provider_id="dataset-1",
                created_by=run.created_by,
                created_at=datetime.now(UTC),
            )
        )
        # C16：Worker 对没有已对账网关策略的租户失败关闭，此处补齐 desired 状态，
        # 让用例仍然验证知识 Provider 装配而不是被网关门禁提前拦下。
        _seed_active_gateway_state(session, tenant_id=run.tenant_id, user_id=run.created_by)
        await session.commit()

    try:
        with pytest.raises(TransientRuntimePreparationError) as captured:
            await resolver.resolve(
                run,
                {
                    "work_mode": "autonomous",
                    "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                    "skill_ids": [],
                    "tool_ids": [],
                    "knowledge_base_ids": [str(knowledge_base_id)],
                },
            )
        assert not isinstance(captured.value, PermanentRuntimePreparationError)
    finally:
        await resolver.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_startup_readiness_uses_the_configured_alias_allowlist() -> None:
    readiness = RecordingGatewayReadiness()
    settings = AppSettings(
        llm_gateway_api_key="internal-gateway-key",
        llm_gateway_allowed_aliases=frozenset({"general-purpose", "test-alias"}),
    )

    await _assert_model_gateway_ready(settings=settings, readiness=readiness)

    assert readiness.aliases == frozenset({"general-purpose", "test-alias"})


@pytest.mark.asyncio
async def test_worker_startup_readiness_uses_the_short_readiness_timeout(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    readiness = RecordingGatewayReadiness()

    def build_probe(**kwargs):
        captured.update(kwargs)
        return readiness

    monkeypatch.setattr(
        worker_main_module,
        "LiteLLMGatewayReadinessProbe",
        build_probe,
    )
    settings = AppSettings(
        llm_gateway_api_key="internal-gateway-key",
        llm_gateway_readiness_timeout_seconds=7,
    )

    await _assert_model_gateway_ready(settings=settings)

    assert captured["timeout_seconds"] == 7


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


@pytest.mark.asyncio
async def test_run_worker_service_configures_audit_hashing_at_startup() -> None:
    """Worker 进程必须在启动处装配审计 HMAC 密钥（与 API create_app 同源）。

    C13 起 worker 投递路径会写审计事件（审批决策落审计）；若 worker 进程未装配
    审计哈希器，写入会 fail-closed 抛 AuditHmacKeyNotConfiguredError，导致投递失败。
    此处以 gateway 未配置的快速失败路径为锚，断言装配发生在启动早期、失败之前。
    """
    from agent_platform.platform.audit.hashing import (
        active_audit_hasher,
        configure_audit_hashing,
    )

    # 模拟全新 worker 进程：显式清空进程级审计哈希器（覆盖 conftest 的默认装配）。
    configure_audit_hashing(None)
    assert active_audit_hasher() is None

    with pytest.raises(WorkerConfigurationError):
        await run_worker_service(runtime_resolver=None)

    assert active_audit_hasher() is not None


@pytest.mark.asyncio
async def test_production_worker_assembly_wires_tenant_attributable_gateway_credentials() -> None:
    """C16：生产 Worker 装配路径必须按租户解析网关凭据，绝不回退应用级共享 Key。

    C07（knowledge_provider_registry 从未注入）与 C13（worker 从未装配审计 HMAC）都是
    生产装配缺口，本用例直接背书 _build_runtime_resolver 的真实装配。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = AppSettings(
        sandbox_controller_secret="sandbox-secret-16chars",
        model_gateway_key_secret="a-strong-model-gateway-key-secret-000001",
    )
    resolver = _build_runtime_resolver(settings=settings, session_factory=session_factory)
    tenant_id, user_id = uuid4(), uuid4()
    async with session_factory() as session:
        _seed_active_gateway_state(session, tenant_id=tenant_id, user_id=user_id)
        await session.commit()

    try:
        model_resolver = resolver._model_resolver
        resolved = await model_resolver.resolve(
            PublishedModel(kind="gateway_alias", alias="general-purpose"),
            tenant_id=tenant_id,
        )
        assert resolved.openai_api_key is not None
        # 该 Key 必须与 Controller 用同一派生函数得到的租户 Key 完全一致（跨进程一致性）
        assert resolved.openai_api_key.get_secret_value() == derive_tenant_gateway_key(
            secret=settings.model_gateway_key_secret,
            tenant_id=tenant_id,
            key_version=1,
        ).get_secret_value()

        # 没有策略的租户：生产装配必须失败关闭，而不是退回共享 Key
        with pytest.raises(ModelGatewayUnavailable) as captured:
            await model_resolver.resolve(
                PublishedModel(kind="gateway_alias", alias="general-purpose"),
                tenant_id=uuid4(),
            )
        assert captured.value.code == "model_gateway_policy_not_provisioned"
    finally:
        await resolver.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_production_worker_assembly_wires_model_usage_recorder() -> None:
    """C16 阶段二：生产 Worker 装配必须注入用量记录器，否则捕获点静默失效
    （与 C07 knowledge_provider_registry 从未注入同型的装配缺口）。"""
    from agent_platform.infrastructure.database.repositories.model_usage import (
        SessionModelUsageRecorder,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = AppSettings(
        sandbox_controller_secret="sandbox-secret-16chars",
        model_gateway_key_secret="a-strong-model-gateway-key-secret-000001",
    )
    resolver = _build_runtime_resolver(settings=settings, session_factory=session_factory)
    try:
        assert isinstance(resolver._model_usage_recorder, SessionModelUsageRecorder)
    finally:
        await resolver.aclose()
        await engine.dispose()
