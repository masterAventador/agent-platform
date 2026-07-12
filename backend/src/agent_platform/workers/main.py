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
from typing import Any, Protocol, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from minio import Minio
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
from agent_platform.infrastructure.mcp.executor import MCPToolExecutor
from agent_platform.infrastructure.mcp.resolver import DatabaseMCPClientResolver
from agent_platform.infrastructure.object_storage.minio import (
    MinioClient,
    MinioSkillStorage,
)
from agent_platform.infrastructure.queue.redis_streams import RedisRunQueue
from agent_platform.platform.tool_gateway import (
    ToolDefinition,
    ToolGateway,
)
from agent_platform.platform.tools.entities import McpServer
from agent_platform.workers.run_worker import RuntimeResolver, RunWorker, WorkerFenced
from agent_platform.workers.runtime_adapters import (
    BuiltinRuntimeAdapters,
    RuntimeAdapterConfigurationError,
    create_runtime_adapters,
)
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    PlatformModelResolver,
    PlatformRuntimeSelector,
)
from agent_platform.workers.runtime_recovery import RuntimeRecoveryTransient


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
                await worker.run_once(block_ms=block_ms)
            except WorkerFenced:
                logger.error("worker_runtime_ownership_fenced")
                stop_event.set()
            except Exception as error:
                logger.error(
                    "worker_delivery_processing_failed",
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
    ready_file: Path = WORKER_READY_FILE,
) -> None:
    if replicas != 1:
        raise WorkerConfigurationError(
            "only a single replica is supported until the runtime registry is durable"
        )

    initialize_database_metadata()
    app_settings = settings or AppSettings()
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
        )
    finally:
        ready_file.unlink(missing_ok=True)
        service_health.ready = False
        await checkpoint_stack.aclose()
        await redis.aclose()
        await engine.dispose()


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
) -> RuntimeResolver:
    adapters = _load_runtime_adapters(settings, session_factory=session_factory)
    tool_reader = _SessionToolReader(session_factory)
    gateway = ToolGateway(
        executor=MCPToolExecutor(DatabaseMCPClientResolver(tool_reader)),
        definition_resolver=tool_reader,
        credential_resolver=adapters.credential_resolver,
        audit_sink=adapters.audit_sink,
    )
    minio = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    return ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=MinioSkillStorage(
            client=cast(MinioClient, minio),
            bucket=settings.skill_storage_bucket,
        ),
        sandbox_manager=adapters.sandbox_manager,
        gateway=gateway,
        runtime_selector=PlatformRuntimeSelector(
            workflow_factory=adapters.workflow_factory,
            checkpointer=checkpointer,
        ),
        sandbox_ttl=timedelta(seconds=settings.sandbox_ttl_seconds),
        close_callback=adapters.aclose,
        model_resolver=model_resolver,
    )


def _checkpoint_url(database_url: str) -> str:
    prefix = "postgresql+asyncpg://"
    if not database_url.startswith(prefix):
        raise WorkerConfigurationError("PostgreSQL asyncpg database URL is required")
    return "postgresql://" + database_url.removeprefix(prefix)


def _load_runtime_adapters(
    settings: AppSettings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> BuiltinRuntimeAdapters:
    try:
        return create_runtime_adapters(
            settings=settings,
            session_factory=session_factory,
        )
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
