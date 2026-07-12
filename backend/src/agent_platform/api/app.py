from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.routes.auth import router as auth_router
from agent_platform.config import AppSettings
from agent_platform.infrastructure.security.passwords import Argon2PasswordHasher
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter
from agent_platform.infrastructure.security.tokens import SessionTokenManager
from agent_platform.platform.auth.ports import AuthRateLimiter


def create_app(
    *,
    settings: AppSettings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    auth_rate_limiter: AuthRateLimiter | None = None,
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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned_engine is not None:
            await owned_engine.dispose()
        if owned_redis is not None:
            await owned_redis.aclose()

    app = FastAPI(title="Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.session_factory = session_factory
    app.state.auth_rate_limiter = auth_rate_limiter
    app.state.password_hasher = Argon2PasswordHasher()
    app.state.session_token_manager = SessionTokenManager()
    app.include_router(auth_router)

    @app.get("/api/v1/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
