import asyncio
import importlib
import os
import signal
import socket
import sys
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from minio import Minio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
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
    CredentialResolver,
    ToolAuditSink,
    ToolDefinition,
    ToolGateway,
)
from agent_platform.platform.tools.entities import McpServer
from agent_platform.runtimes.base import RunWorkspaceFactory
from agent_platform.workers.run_worker import RuntimeResolver, RunWorker
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    PlatformRuntimeSelector,
    RuntimeFactory,
)


class WorkerConfigurationError(Exception):
    """Worker 缺少安全运行所需的明确装配。"""


class WorkerLoop(Protocol):
    async def run_once(self, *, block_ms: int = 5_000) -> bool: ...


class RuntimeAdapters(Protocol):
    workspace_factory: RunWorkspaceFactory
    autonomous_factory: RuntimeFactory
    workflow_factory: RuntimeFactory
    credential_resolver: CredentialResolver
    audit_sink: ToolAuditSink


class RuntimeAdapterFactory(Protocol):
    def __call__(self, settings: AppSettings) -> RuntimeAdapters: ...


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
) -> None:
    health.live = True
    health.ready = True
    try:
        while not stop_event.is_set():
            await worker.run_once(block_ms=block_ms)
    finally:
        health.ready = False
        health.live = False


async def run_worker_service(
    *,
    runtime_resolver: RuntimeResolver | None = None,
    settings: AppSettings | None = None,
    stop_event: asyncio.Event | None = None,
    health: WorkerHealth | None = None,
    consumer_name: str | None = None,
    replicas: int = 1,
) -> None:
    if replicas != 1:
        raise WorkerConfigurationError(
            "only a single replica is supported until the runtime registry is durable"
        )

    app_settings = settings or AppSettings()
    engine = create_async_engine(app_settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
    queue = RedisRunQueue(redis)
    service_stop = stop_event or asyncio.Event()
    service_health = health or WorkerHealth()
    _install_signal_handlers(service_stop)
    try:
        resolver = runtime_resolver or _build_runtime_resolver(
            settings=app_settings,
            session_factory=session_factory,
        )
        await queue.setup()
        worker = RunWorker(
            session_factory=session_factory,
            queue=queue,
            runtime_resolver=resolver,
            consumer_name=consumer_name or _default_consumer_name(),
        )
        await serve(worker=worker, stop_event=service_stop, health=service_health)
    finally:
        service_health.ready = False
        await redis.aclose()
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
) -> RuntimeResolver:
    adapters = _load_runtime_adapters(settings)
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
        workspace_factory=adapters.workspace_factory,
        gateway=gateway,
        runtime_selector=PlatformRuntimeSelector(
            autonomous_factory=adapters.autonomous_factory,
            workflow_factory=adapters.workflow_factory,
        ),
    )


def _load_runtime_adapters(settings: AppSettings) -> RuntimeAdapters:
    target = os.getenv("AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY", "").strip()
    if not target:
        raise WorkerConfigurationError(
            "a concrete runtime resolver adapter factory with model, safe workspace/sandbox, "
            "DeepAgent, and LangGraph factories is required"
        )
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise WorkerConfigurationError(
            "AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY must use module:attribute syntax"
        )
    try:
        factory = cast(
            RuntimeAdapterFactory,
            getattr(importlib.import_module(module_name), attribute),
        )
        adapters = factory(settings)
    except Exception as error:
        raise WorkerConfigurationError("runtime adapter factory could not be loaded") from error
    required_capabilities = (
        callable(getattr(adapters, "autonomous_factory", None)),
        callable(getattr(adapters, "workflow_factory", None)),
        callable(getattr(getattr(adapters, "workspace_factory", None), "create", None)),
        callable(getattr(getattr(adapters, "credential_resolver", None), "resolve", None)),
        callable(getattr(getattr(adapters, "audit_sink", None), "emit", None)),
    )
    if not all(required_capabilities):
        raise WorkerConfigurationError(
            "runtime adapter factory result does not provide required capabilities"
        )
    return adapters


def main() -> None:
    replicas = int(os.getenv("AGENT_PLATFORM_WORKER_REPLICAS", "1"))
    try:
        asyncio.run(run_worker_service(replicas=replicas))
    except WorkerConfigurationError as error:
        print(f"worker configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
