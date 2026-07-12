import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.auth import (
    AuthSessionRecord,
    SqlAlchemyAuthSessionRepository,
    SqlAlchemyUserRepository,
    UserRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyWorkspaceRepository,
)
from agent_platform.infrastructure.security.passwords import Argon2PasswordHasher
from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter
from agent_platform.infrastructure.security.tokens import SessionTokenManager
from agent_platform.platform.auth.errors import RateLimitExceeded
from agent_platform.platform.auth.services import AuthService


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest.mark.asyncio
async def test_password_and_session_secrets_are_not_stored_in_plaintext() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    password = "correct horse battery staple"

    async with session_factory() as database_session:
        service = AuthService(
            users=SqlAlchemyUserRepository(database_session),
            sessions=SqlAlchemyAuthSessionRepository(database_session),
            password_hasher=Argon2PasswordHasher(),
            rate_limiter=AllowAllRateLimiter(),
            token_manager=SessionTokenManager(),
            session_ttl_seconds=3600,
            require_email_verification=False,
            workspaces=SqlAlchemyWorkspaceRepository(database_session),
        )
        await service.register(email="secure@example.com", password=password)
        issued_session = await service.login(email="secure@example.com", password=password)

        user_record = (await database_session.execute(select(UserRecord))).scalar_one()
        session_record = (
            await database_session.execute(select(AuthSessionRecord))
        ).scalar_one()

    assert user_record.password_hash != password
    assert user_record.password_hash.startswith("$argon2id$")
    assert session_record.token_digest != issued_session.raw_token
    assert session_record.token_digest == SessionTokenManager().digest(issued_session.raw_token)
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_enforces_case_insensitive_email_uniqueness() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as database_session:
        database_session.add_all(
            [
                UserRecord(
                    id=UUID("00000000-0000-0000-0000-000000000001"),
                    email="CaseSensitive@example.com",
                    password_hash="hash",
                    email_verified=False,
                    created_at=datetime.now(UTC),
                ),
                UserRecord(
                    id=UUID("00000000-0000-0000-0000-000000000002"),
                    email="casesensitive@example.com",
                    password_hash="hash",
                    email_verified=False,
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await database_session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_redis_rate_limiter_blocks_requests_over_limit() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis 限流测试")

    redis = Redis.from_url(redis_url, decode_responses=True)
    limiter = RedisAuthRateLimiter(redis, register_limit=2, login_limit=2)
    unique_key = f"rate-limit-{uuid4()}"

    await limiter.ensure_allowed(scope="login", key=unique_key)
    await limiter.ensure_allowed(scope="login", key=unique_key)
    with pytest.raises(RateLimitExceeded):
        await limiter.ensure_allowed(scope="login", key=unique_key)

    await redis.aclose()
