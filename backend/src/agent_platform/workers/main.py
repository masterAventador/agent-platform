import asyncio
import logging
import os
import signal
import socket
import sys
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.checkpoints.postgres import postgres_checkpointer
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnershipBusy,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.infrastructure.llm.litellm import (
    LiteLLMChatModelFactory,
    LiteLLMGatewayReadinessProbe,
    ModelGatewayConfigurationError,
    ModelGatewayReadiness,
    ModelGatewayReadinessError,
)
from agent_platform.infrastructure.mcp.executor import (
    MCPToolExecutor,
    ResilientToolExecutor,
)
from agent_platform.infrastructure.mcp.resolver import (
    AllowlistStdioExecutionPolicy,
    DatabaseMCPClientResolver,
)
from agent_platform.infrastructure.object_storage.artifacts import (
    create_artifact_storage_provider,
    create_bounded_minio_client,
)
from agent_platform.infrastructure.object_storage.minio import (
    MinioClient,
    MinioSkillStorage,
)
from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue
from agent_platform.knowledge.ragflow import RagFlowClient
from agent_platform.observability.metrics import OperationalComponent, OperationalMetrics
from agent_platform.observability.telemetry import configure_telemetry
from agent_platform.platform.audit.hashing import configure_audit_hashing
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry
from agent_platform.platform.tool_gateway import (
    InMemoryToolCircuitBreaker,
    ToolDefinition,
    ToolGateway,
)
from agent_platform.platform.tools.entities import McpServer
from agent_platform.runtimes.recovery import RuntimeRecoveryTransient
from agent_platform.workers.run_worker import RuntimeResolver, RunWorker, WorkerFenced
from agent_platform.workers.runtime_adapters import (
    BuiltinRuntimeAdapters,
    RuntimeAdapterConfigurationError,
    create_runtime_adapters,
    validate_runtime_adapter_configuration,
)
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    PlatformModelResolver,
    PlatformRuntimeSelector,
)


class WorkerConfigurationError(Exception):
    """Worker 缺少安全运行所需的明确装配。"""


logger = logging.getLogger(__name__)
WORKER_READY_FILE = Path("/tmp/agent-platform-worker-ready")


class WorkerLoop(Protocol):
    async def run_once(self, *, block_ms: int = 5_000) -> bool: ...

    async def renew_active_runtimes(self) -> None: ...

    async def aclose(self) -> None: ...


class RuntimeRecoveryWorker(Protocol):
    async def recover_incomplete_runs(self) -> int: ...


@dataclass(slots=True)
class WorkerHealth:
    """供进程宿主暴露的最小存活/就绪状态。"""

    live: bool = False
    ready: bool = False
    single_replica: bool = True


async def serve(
    *,
    worker: WorkerLoop,
    stop_event: asyncio.Event,
    health: WorkerHealth,
    block_ms: int = 5_000,
    retry_backoff_seconds: float = 1.0,
    heartbeat_interval_seconds: float | None = None,
    metrics: OperationalMetrics | None = None,
) -> None:
    health.live = True
    health.ready = True
    heartbeat_task = (
        asyncio.create_task(
            _heartbeat_loop(
                worker=worker,
                stop_event=stop_event,
                interval_seconds=heartbeat_interval_seconds,
            )
        )
        if heartbeat_interval_seconds is not None
        else None
    )
    try:
        while not stop_event.is_set():
            try:
                started = perf_counter()
                processed = await worker.run_once(block_ms=block_ms)
                if processed and metrics is not None:
                    metrics.record(
                        component=OperationalComponent.WORKER,
                        operation="run",
                        outcome="succeeded",
                        duration_ms=(perf_counter() - started) * 1_000,
                    )
            except WorkerFenced:
                if metrics is not None:
                    metrics.record(
                        component=OperationalComponent.WORKER,
                        operation="run",
                        outcome="failed",
                        duration_ms=(perf_counter() - started) * 1_000,
                    )
                logger.error("worker_runtime_ownership_fenced")
                stop_event.set()
            except Exception as error:
                if metrics is not None:
                    metrics.record(
                        component=OperationalComponent.WORKER,
                        operation="run",
                        outcome="failed",
                        duration_ms=(perf_counter() - started) * 1_000,
                    )
                logger.error(
                    "worker_delivery_processing_failed error_type=%s",
                    type(error).__name__,
                    extra={"error_type": type(error).__name__},
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=retry_backoff_seconds,
                    )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        await worker.aclose()
        health.ready = False
        health.live = False


async def _heartbeat_loop(
    *, worker: WorkerLoop, stop_event: asyncio.Event, interval_seconds: float
) -> None:
    while not stop_event.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        if stop_event.is_set():
            return
        try:
            await worker.renew_active_runtimes()
        except Exception as error:
            logger.error(
                "worker_sandbox_heartbeat_failed",
                extra={"error_type": type(error).__name__},
            )
            stop_event.set()
            return


async def wait_for_runtime_recovery(
    *,
    worker: RuntimeRecoveryWorker,
    stop_event: asyncio.Event,
    retry_seconds: float,
) -> int | None:
    while not stop_event.is_set():
        try:
            return await worker.recover_incomplete_runs()
        except (RuntimeOwnershipBusy, RuntimeRecoveryTransient) as error:
            logger.warning(
                "worker_runtime_recovery_waiting",
                extra={"error_type": type(error).__name__},
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=retry_seconds)
    return None


async def run_worker_service(
    *,
    runtime_resolver: RuntimeResolver | None = None,
    settings: AppSettings | None = None,
    stop_event: asyncio.Event | None = None,
    health: WorkerHealth | None = None,
    consumer_name: str | None = None,
    replicas: int = 1,
    model_resolver: PlatformModelResolver | None = None,
    gateway_readiness: ModelGatewayReadiness | None = None,
    ready_file: Path = WORKER_READY_FILE,
) -> None:
    if replicas != 1:
        raise WorkerConfigurationError(
            "only a single replica is supported until the runtime registry is durable"
        )

    initialize_database_metadata()
    app_settings = settings or AppSettings()
    # 与 API create_app 同源装配审计 HMAC 密钥：worker 投递路径会写审计事件
    # （审批决策落审计），未装配时审计写入 fail-closed，投递会失败。
    configure_audit_hashing(app_settings.audit_hmac_key.get_secret_value())
    telemetry = configure_telemetry(app_settings)
    telemetry.instrument_libraries()
    if runtime_resolver is None and model_resolver is None:
        await _assert_model_gateway_ready(
            settings=app_settings,
            readiness=gateway_readiness,
            metrics=telemetry.operational_metrics,
        )
    if runtime_resolver is None:
        _assert_runtime_adapters_configured(app_settings)
    engine = create_async_engine(app_settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
    queue = RedisRunQueue(
        redis,
        stream_name=app_settings.run_queue_stream_name,
        group_name=app_settings.run_queue_group_name,
        pending_min_idle_ms=app_settings.queue_pending_min_idle_ms,
        dead_letter_stream_name=app_settings.run_queue_dead_letter_stream_name,
        max_delivery_attempts=app_settings.queue_max_delivery_attempts,
        metrics=telemetry.operational_metrics,
    )
    service_stop = stop_event or asyncio.Event()
    service_health = health or WorkerHealth()
    ready_file.unlink(missing_ok=True)
    _install_signal_handlers(service_stop)
    checkpoint_stack = AsyncExitStack()
    try:
        if runtime_resolver is None:
            checkpointer = await checkpoint_stack.enter_async_context(
                postgres_checkpointer(_checkpoint_url(app_settings.database_url))
            )
            await checkpointer.setup()
            resolver = _build_runtime_resolver(
                settings=app_settings,
                session_factory=session_factory,
                model_resolver=model_resolver,
                checkpointer=checkpointer,
                metrics=telemetry.operational_metrics,
            )
        else:
            resolver = runtime_resolver
        await queue.setup()
        worker = RunWorker(
            session_factory=session_factory,
            queue=queue,
            runtime_resolver=resolver,
            consumer_name=consumer_name or _default_consumer_name(),
            runtime_lease_duration=timedelta(seconds=app_settings.runtime_lease_seconds),
            cancellation_poll_initial_seconds=(app_settings.runtime_cancel_poll_initial_seconds),
            cancellation_poll_max_seconds=app_settings.runtime_cancel_poll_max_seconds,
            approval_pending_timeout_seconds=app_settings.approval_pending_timeout_seconds,
        )
        recovered = await wait_for_runtime_recovery(
            worker=worker,
            stop_event=service_stop,
            retry_seconds=min(1.0, app_settings.runtime_heartbeat_seconds),
        )
        if recovered is None:
            return
        ready_file.touch(mode=0o600)
        await serve(
            worker=worker,
            stop_event=service_stop,
            health=service_health,
            retry_backoff_seconds=app_settings.worker_retry_backoff_seconds,
            heartbeat_interval_seconds=app_settings.runtime_heartbeat_seconds,
            metrics=telemetry.operational_metrics,
        )
    finally:
        ready_file.unlink(missing_ok=True)
        service_health.ready = False
        await checkpoint_stack.aclose()
        await redis.aclose()
        await engine.dispose()
        telemetry.shutdown()


async def check_worker_configuration(settings: AppSettings | None = None) -> None:
    initialize_database_metadata()
    app_settings = settings or AppSettings()
    engine = create_async_engine(app_settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    resolver: RuntimeResolver | None = None
    try:
        resolver = _build_runtime_resolver(
            settings=app_settings,
            session_factory=session_factory,
        )
    finally:
        close = getattr(resolver, "aclose", None)
        if callable(close):
            await close()
        await engine.dispose()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


def _default_consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class _SessionToolReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(
        self,
        *,
        tenant_id: UUID,
        tool_id: UUID,
    ) -> ToolDefinition | None:
        async with self._session_factory() as session:
            return await SqlAlchemyToolRepository(session).resolve(
                tenant_id=tenant_id,
                tool_id=tool_id,
            )

    async def get_server(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
    ) -> McpServer | None:
        async with self._session_factory() as session:
            return await SqlAlchemyToolRepository(session).get_server(
                tenant_id=tenant_id,
                server_id=server_id,
            )


def _build_runtime_resolver(
    *,
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    model_resolver: PlatformModelResolver | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    metrics: OperationalMetrics | None = None,
) -> RuntimeResolver:
    if model_resolver is None:
        try:
            model_resolver = PlatformModelResolver(
                model_factory=LiteLLMChatModelFactory(
                    base_url=settings.llm_gateway_url,
                    api_key=settings.llm_gateway_api_key,
                    timeout_seconds=settings.llm_gateway_request_timeout_seconds,
                    max_retries=settings.llm_gateway_max_retries,
                ),
                allowed_aliases=settings.llm_gateway_allowed_aliases,
            )
        except ModelGatewayConfigurationError as error:
            raise WorkerConfigurationError("model gateway is not configured") from error
    adapters = _load_runtime_adapters(
        settings,
        session_factory=session_factory,
        metrics=metrics,
    )
    tool_reader = _SessionToolReader(session_factory)
    gateway = ToolGateway(
        executor=ResilientToolExecutor(
            MCPToolExecutor(
                DatabaseMCPClientResolver(
                    tool_reader,
                    stdio_policy=AllowlistStdioExecutionPolicy(
                        settings.mcp_stdio_allowed_commands
                    ),
                    timeout_seconds=settings.tool_invocation_timeout_seconds,
                )
            ),
            max_read_retries=settings.tool_invocation_max_read_retries,
        ),
        definition_resolver=tool_reader,
        credential_resolver=adapters.credential_resolver,
        audit_sink=adapters.audit_sink,
        execution_circuit=InMemoryToolCircuitBreaker(
            failure_threshold=settings.tool_circuit_failure_threshold,
            cooldown_seconds=settings.tool_circuit_cooldown_seconds,
        ),
    )
    minio = create_bounded_minio_client(settings)
    knowledge_provider = RagFlowClient(
        base_url=settings.ragflow_url,
        api_key=settings.ragflow_api_key,
        metrics=metrics,
    )

    async def close_adapters_and_knowledge_provider() -> None:
        try:
            await adapters.aclose()
        finally:
            await knowledge_provider.aclose()

    return ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=MinioSkillStorage(
            client=cast(MinioClient, minio),
            bucket=settings.skill_storage_bucket,
        ),
        artifact_storage=create_artifact_storage_provider(
            settings=settings,
            minio_client=cast(MinioClient, minio),
        ),
        sandbox_manager=adapters.sandbox_manager,
        gateway=gateway,
        runtime_selector=PlatformRuntimeSelector(
            workflow_factory=adapters.workflow_factory,
            checkpointer=checkpointer,
        ),
        sandbox_ttl=timedelta(seconds=settings.sandbox_ttl_seconds),
        artifact_operation_lease_duration=timedelta(
            seconds=settings.artifact_storage_operation_lease_seconds
        ),
        artifact_operation_heartbeat_interval=(
            settings.artifact_storage_operation_heartbeat_seconds
        ),
        artifact_storage_request_timeout=(settings.artifact_storage_request_timeout_seconds),
        knowledge_provider_registry=KnowledgeProviderRegistry([knowledge_provider]),
        close_callback=close_adapters_and_knowledge_provider,
        model_resolver=model_resolver,
    )


async def _assert_model_gateway_ready(
    *,
    settings: AppSettings,
    readiness: ModelGatewayReadiness | None = None,
    metrics: OperationalMetrics | None = None,
) -> None:
    try:
        probe = readiness or LiteLLMGatewayReadinessProbe(
            base_url=settings.llm_gateway_url,
            api_key=settings.llm_gateway_api_key,
            timeout_seconds=settings.llm_gateway_readiness_timeout_seconds,
            metrics=metrics,
        )
        await probe.assert_ready(settings.llm_gateway_allowed_aliases)
    except (ModelGatewayConfigurationError, ModelGatewayReadinessError) as error:
        raise WorkerConfigurationError("model gateway is not ready") from error


def _checkpoint_url(database_url: str) -> str:
    prefix = "postgresql+asyncpg://"
    if not database_url.startswith(prefix):
        raise WorkerConfigurationError("PostgreSQL asyncpg database URL is required")
    return "postgresql://" + database_url.removeprefix(prefix)


def _load_runtime_adapters(
    settings: AppSettings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    metrics: OperationalMetrics | None = None,
) -> BuiltinRuntimeAdapters:
    try:
        return create_runtime_adapters(
            settings=settings,
            session_factory=session_factory,
            metrics=metrics,
        )
    except (RuntimeAdapterConfigurationError, ValueError) as error:
        raise WorkerConfigurationError("builtin runtime adapters are not configured") from error


def _assert_runtime_adapters_configured(settings: AppSettings) -> None:
    try:
        validate_runtime_adapter_configuration(settings)
    except (RuntimeAdapterConfigurationError, ValueError) as error:
        raise WorkerConfigurationError("builtin runtime adapters are not configured") from error


def main() -> None:
    replicas = int(os.getenv("AGENT_PLATFORM_WORKER_REPLICAS", "1"))
    try:
        if os.getenv("AGENT_PLATFORM_WORKER_CONFIG_CHECK") == "1":
            asyncio.run(check_worker_configuration())
        else:
            asyncio.run(run_worker_service(replicas=replicas))
    except WorkerConfigurationError as error:
        print(f"worker configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
