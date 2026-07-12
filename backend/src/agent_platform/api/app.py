from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from minio import Minio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.routes.auth import router as auth_router
from agent_platform.api.routes.employees import router as employees_router
from agent_platform.api.routes.knowledge import router as knowledge_router
from agent_platform.api.routes.runs import router as runs_router
from agent_platform.api.routes.skills import router as skills_router
from agent_platform.config import AppSettings
from agent_platform.infrastructure.object_storage.minio import (
    MinioClient,
    MinioSkillStorage,
)
from agent_platform.infrastructure.security.passwords import Argon2PasswordHasher
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter
from agent_platform.infrastructure.security.tokens import SessionTokenManager
from agent_platform.knowledge.ragflow import RagFlowClient, RagFlowError
from agent_platform.platform.auth.ports import AuthRateLimiter
from agent_platform.platform.knowledge.ports import KnowledgeProvider
from agent_platform.platform.skills.ports import SkillStorage


def create_app(
    *,
    settings: AppSettings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    auth_rate_limiter: AuthRateLimiter | None = None,
    knowledge_provider: KnowledgeProvider | None = None,
    skill_storage: SkillStorage | None = None,
) -> FastAPI:
    app_settings = settings or AppSettings()
    owned_engine = None
    if session_factory is None:
        owned_engine = create_async_engine(app_settings.database_url)
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
        )
        knowledge_provider = owned_knowledge_provider

    if skill_storage is None:
        minio_client = Minio(
            app_settings.minio_endpoint,
            access_key=app_settings.minio_access_key,
            secret_key=app_settings.minio_secret_key,
            secure=app_settings.minio_secure,
        )
        skill_storage = MinioSkillStorage(
            client=cast(MinioClient, minio_client),
            bucket=app_settings.skill_storage_bucket,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned_engine is not None:
            await owned_engine.dispose()
        if owned_redis is not None:
            await owned_redis.aclose()
        if owned_knowledge_provider is not None:
            await owned_knowledge_provider.aclose()

    app = FastAPI(title="Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.auth_rate_limiter = auth_rate_limiter
    app.state.password_hasher = Argon2PasswordHasher()
    app.state.session_token_manager = SessionTokenManager()
    app.state.knowledge_provider = knowledge_provider
    app.state.skill_storage = skill_storage
    app.include_router(auth_router)
    app.include_router(employees_router)
    app.include_router(runs_router)
    app.include_router(knowledge_router)
    app.include_router(skills_router)

    @app.exception_handler(RagFlowError)
    async def handle_ragflow_error(_: Request, __: RagFlowError) -> JSONResponse:
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

    return app


app = create_app()
