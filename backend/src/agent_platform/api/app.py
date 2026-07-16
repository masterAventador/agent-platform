import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from minio import Minio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.api.middleware.request_body_limit import (
    FileUploadRequestBodyLimitMiddleware,
)
from agent_platform.api.routes.artifacts import router as artifacts_router
from agent_platform.api.routes.audit import router as audit_router
from agent_platform.api.routes.auth import router as auth_router
from agent_platform.api.routes.conversations import router as conversations_router
from agent_platform.api.routes.dead_letters import router as dead_letters_router
from agent_platform.api.routes.employees import router as employees_router
from agent_platform.api.routes.knowledge import router as knowledge_router
from agent_platform.api.routes.model_gateway import router as model_gateway_router
from agent_platform.api.routes.observability import router as observability_router
from agent_platform.api.routes.runs import router as runs_router
from agent_platform.api.routes.skills import router as skills_router
from agent_platform.api.routes.tools import invocation_router, mcp_router, tool_router
from agent_platform.api.routes.workbench import router as workbench_router
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata
from agent_platform.infrastructure.database.engine import create_database_engine
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyArtifactRepository,
    SqlAlchemyArtifactStorageOperationRepository,
    SqlAlchemyFileRepository,
)
from agent_platform.infrastructure.database.repositories.audit import (
    purge_expired_audit_events,
)
from agent_platform.infrastructure.mcp.probe import MCPCatalogProbe
from agent_platform.infrastructure.mcp.resolver import AllowlistStdioExecutionPolicy
from agent_platform.infrastructure.object_storage.artifacts import (
    create_artifact_storage_provider,
    create_bounded_minio_client,
)
from agent_platform.infrastructure.object_storage.minio import (
    MinioClient,
    MinioSkillStorage,
)
from agent_platform.infrastructure.secrets import (
    LocalFileCredentialResolver,
    LocalFileCredentialStore,
)
from agent_platform.infrastructure.security.passwords import Argon2PasswordHasher
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter
from agent_platform.infrastructure.security.tokens import SessionTokenManager
from agent_platform.knowledge.ragflow import RagFlowClient
from agent_platform.observability.correlation import (
    bind_correlation_id,
    reset_correlation_id,
)
from agent_platform.observability.telemetry import Telemetry, configure_telemetry
from agent_platform.platform.artifacts.ports import ArtifactStorageProvider
from agent_platform.platform.artifacts.services import ArtifactService
from agent_platform.platform.auth.ports import AuthRateLimiter
from agent_platform.platform.knowledge.errors import (
    InvalidKnowledgeProviderResponse,
    KnowledgeProviderUnavailable,
)
from agent_platform.platform.knowledge.ports import KnowledgeProvider
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry
from agent_platform.platform.skills.ports import SkillStorage
from agent_platform.platform.tools.ports import (
    McpConnectionProbe,
    ToolCredentialResolver,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def _wait_for_database_ready(
    session_factory: SessionFactory,
    *,
    retry_delay_seconds: float = 1.0,
) -> None:
    waiting_logged = False
    while True:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1 FROM artifact_storage_operations LIMIT 1"))
            if waiting_logged:
                logger.info("artifact_storage_reconciliation_database_ready")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not waiting_logged:
                waiting_logged = True
                logger.warning(
                    "artifact_storage_reconciliation_waiting_for_schema",
                    extra={"error_type": type(exc).__name__},
                )
            await asyncio.sleep(retry_delay_seconds)


def create_app(
    *,
    settings: AppSettings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    auth_rate_limiter: AuthRateLimiter | None = None,
    knowledge_provider: KnowledgeProvider | None = None,
    skill_storage: SkillStorage | None = None,
    artifact_storage: ArtifactStorageProvider | None = None,
    telemetry: Telemetry | None = None,
    mcp_connection_probe: McpConnectionProbe | None = None,
    tool_credential_store: LocalFileCredentialStore | None = None,
    tool_credential_resolver: ToolCredentialResolver | None = None,
) -> FastAPI:
    initialize_database_metadata()
    app_settings = settings or AppSettings()
    app_telemetry = telemetry or configure_telemetry(app_settings)
    app_telemetry.instrument_libraries()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_database_engine(app_settings.database_url)
        session_factory = async_sessionmaker(owned_engine, expire_on_commit=False)

    owned_redis: Redis | None = None
    if auth_rate_limiter is None:
        owned_redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
        auth_rate_limiter = RedisAuthRateLimiter(
            owned_redis,
            register_limit=app_settings.auth_register_limit_per_minute,
            login_limit=app_settings.auth_login_limit_per_minute,
        )

    owned_knowledge_provider: RagFlowClient | None = None
    if knowledge_provider is None:
        owned_knowledge_provider = RagFlowClient(
            base_url=app_settings.ragflow_url,
            api_key=app_settings.ragflow_api_key,
            metrics=app_telemetry.operational_metrics,
        )
        knowledge_provider = owned_knowledge_provider

    minio_client: Minio | None = None
    if skill_storage is None or artifact_storage is None:
        minio_client = create_bounded_minio_client(app_settings)
    if skill_storage is None:
        skill_storage = MinioSkillStorage(
            client=cast(MinioClient, minio_client), bucket=app_settings.skill_storage_bucket
        )
    if artifact_storage is None:
        artifact_storage = create_artifact_storage_provider(
            settings=app_settings,
            minio_client=cast(MinioClient, minio_client) if minio_client is not None else None,
        )
    configured_session_factory = session_factory
    configured_artifact_storage = artifact_storage

    async def reconcile_artifact_storage() -> None:
        await _wait_for_database_ready(configured_session_factory)
        next_unbound_cleanup_at = 0.0
        while True:
            try:
                async with configured_session_factory() as session:
                    service = ArtifactService(
                        file_repository=SqlAlchemyFileRepository(session),
                        artifact_repository=SqlAlchemyArtifactRepository(session),
                        operation_repository=(
                            SqlAlchemyArtifactStorageOperationRepository(
                                session,
                                heartbeat_session_factory=configured_session_factory,
                            )
                        ),
                        storage=configured_artifact_storage,
                        operation_lease_duration=timedelta(
                            seconds=app_settings.artifact_storage_operation_lease_seconds
                        ),
                        operation_heartbeat_interval=(
                            app_settings.artifact_storage_operation_heartbeat_seconds
                        ),
                        storage_request_timeout=(
                            app_settings.artifact_storage_request_timeout_seconds
                        ),
                        tombstone_observation_duration=timedelta(
                            seconds=(app_settings.artifact_storage_tombstone_observation_seconds)
                        ),
                        tombstone_rescan_interval=timedelta(
                            seconds=app_settings.artifact_storage_tombstone_rescan_seconds
                        ),
                    )
                    await service.reconcile_pending(commit=session.commit)
                    loop_time = asyncio.get_running_loop().time()
                    if loop_time >= next_unbound_cleanup_at:
                        next_unbound_cleanup_at = loop_time + (
                            app_settings.artifact_unbound_file_cleanup_interval_seconds
                        )
                        await service.cleanup_unbound_files(
                            older_than=datetime.now(UTC)
                            - timedelta(seconds=app_settings.artifact_unbound_file_ttl_seconds),
                            commit=session.commit,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("artifact_storage_reconciliation_failed")
            await asyncio.sleep(5)

    async def sweep_audit_retention() -> None:
        await _wait_for_database_ready(configured_session_factory)
        while True:
            try:
                result = await purge_expired_audit_events(
                    configured_session_factory,
                    cutoff=datetime.now(UTC)
                    - timedelta(days=app_settings.audit_retention_days),
                    limit=app_settings.audit_retention_sweep_batch_limit,
                )
                if result.purged_events:
                    logger.info(
                        "audit_retention_sweep_purged",
                        extra={"purged_events": result.purged_events},
                    )
                if result.failed_tenants:
                    logger.warning(
                        "audit_retention_sweep_partial_failure",
                        extra={"failed_tenants": result.failed_tenants},
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("audit_retention_sweep_failed")
            await asyncio.sleep(app_settings.audit_retention_sweep_interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        reconciliation_task = asyncio.create_task(reconcile_artifact_storage())
        audit_retention_task = asyncio.create_task(sweep_audit_retention())
        try:
            yield
        finally:
            audit_retention_task.cancel()
            reconciliation_task.cancel()
            with suppress(asyncio.CancelledError):
                await audit_retention_task
            with suppress(asyncio.CancelledError):
                await reconciliation_task
            try:
                if owned_engine is not None:
                    await owned_engine.dispose()
                if owned_redis is not None:
                    await owned_redis.aclose()
                if owned_knowledge_provider is not None:
                    await owned_knowledge_provider.aclose()
            finally:
                app_telemetry.shutdown()

    app = FastAPI(title="Agent Platform", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def assign_correlation_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = uuid4().hex
        token = bind_correlation_id(correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = correlation_id
            return response
        finally:
            reset_correlation_id(token)

    app.add_middleware(FileUploadRequestBodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Tenant-ID",
        ],
    )
    app.state.settings = app_settings
    app.state.telemetry = app_telemetry
    app.state.session_factory = session_factory
    app.state.auth_rate_limiter = auth_rate_limiter
    app.state.password_hasher = Argon2PasswordHasher()
    app.state.session_token_manager = SessionTokenManager()
    app.state.knowledge_provider = knowledge_provider
    app.state.knowledge_provider_registry = KnowledgeProviderRegistry([knowledge_provider])
    app.state.skill_storage = skill_storage
    app.state.artifact_storage = artifact_storage
    if mcp_connection_probe is None:
        mcp_connection_probe = MCPCatalogProbe(
            timeout_seconds=app_settings.mcp_connection_timeout_seconds,
            stdio_policy=AllowlistStdioExecutionPolicy(
                app_settings.mcp_stdio_allowed_commands
            ),
        )
    credentials_repository_root = app_settings.local_credentials_repository_root or "."
    if tool_credential_store is None:
        tool_credential_store = LocalFileCredentialStore(
            credentials_file=app_settings.local_credentials_file,
            repository_root=credentials_repository_root,
        )
    if tool_credential_resolver is None:
        tool_credential_resolver = LocalFileCredentialResolver(
            credentials_file=app_settings.local_credentials_file,
            repository_root=credentials_repository_root,
        )
    app.state.mcp_connection_probe = mcp_connection_probe
    app.state.tool_credential_store = tool_credential_store
    app.state.tool_credential_resolver = tool_credential_resolver
    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(employees_router)
    app.include_router(conversations_router)
    app.include_router(runs_router)
    app.include_router(artifacts_router)
    app.include_router(dead_letters_router)
    app.include_router(knowledge_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)
    app.include_router(tool_router)
    app.include_router(invocation_router)
    app.include_router(model_gateway_router)
    app.include_router(observability_router)
    app.include_router(workbench_router)

    @app.exception_handler(KnowledgeProviderUnavailable)
    @app.exception_handler(InvalidKnowledgeProviderResponse)
    async def handle_knowledge_provider_error(
        _: Request,
        __: KnowledgeProviderUnavailable | InvalidKnowledgeProviderResponse,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": {
                    "code": "knowledge_provider_unavailable",
                    "message": "知识服务暂时不可用，请稍后重试",
                }
            },
        )

    @app.get("/api/v1/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    app_telemetry.instrument_app(app)
    return app


app = create_app()
