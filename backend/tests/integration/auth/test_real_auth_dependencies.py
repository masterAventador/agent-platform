import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def real_dependency_urls() -> tuple[str, str]:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if database_url is None or redis_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 和 TEST_REDIS_URL 才运行真实认证集成测试")

    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url, redis_url


@pytest.mark.asyncio
async def test_auth_flow_with_postgres_and_redis(
    real_dependency_urls: tuple[str, str],
) -> None:
    database_url, redis_url = real_dependency_urls
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    rate_limiter = RedisAuthRateLimiter(redis, register_limit=5, login_limit=10)
    app = create_app(
        settings=AppSettings(
            database_url=database_url,
            redis_url=redis_url,
            auth_cookie_secure=False,
            require_email_verification=False,
        ),
        session_factory=session_factory,
        auth_rate_limiter=rate_limiter,
    )
    email = f"real-auth-{uuid4()}@example.com"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        assert (
            await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "correct horse battery staple"},
            )
        ).status_code == 201
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "correct horse battery staple"},
            )
        ).status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        assert (await client.post("/api/v1/auth/logout")).status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async with session_factory() as database_session:
        database_session.add(
            UserRecord(
                id=uuid4(),
                email=email.upper(),
                password_hash="not-a-real-password-hash",
                email_verified=False,
                created_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            await database_session.commit()

    await redis.aclose()
    await engine.dispose()
