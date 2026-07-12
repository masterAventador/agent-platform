from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.platform.auth.errors import RateLimitExceeded


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class RejectAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key
        raise RateLimitExceeded


@pytest_asyncio.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = AppSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        auth_cookie_secure=False,
        auth_session_ttl_seconds=3600,
        require_email_verification=False,
    )
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    await engine.dispose()


@pytest.mark.asyncio
async def test_register_login_restore_session_and_logout(auth_client: AsyncClient) -> None:
    register_response = await auth_client.post(
        "/api/v1/auth/register",
        json={"email": "  Owner@Example.COM  ", "password": "correct horse battery staple"},
    )

    assert register_response.status_code == 201
    assert register_response.json()["email"] == "owner@example.com"
    assert register_response.json()["email_verified"] is False

    login_response = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "OWNER@example.com", "password": "correct horse battery staple"},
    )

    assert login_response.status_code == 200
    assert login_response.json()["email"] == "owner@example.com"
    session_cookie = login_response.headers["set-cookie"]
    assert "agent_platform_session=" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie

    current_user_response = await auth_client.get("/api/v1/auth/me")
    assert current_user_response.status_code == 200
    assert current_user_response.json()["email"] == "owner@example.com"

    logout_response = await auth_client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204

    signed_out_response = await auth_client.get("/api/v1/auth/me")
    assert signed_out_response.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_email_registration_is_rejected(auth_client: AsyncClient) -> None:
    payload = {"email": "member@example.com", "password": "correct horse battery staple"}
    assert (await auth_client.post("/api/v1/auth/register", json=payload)).status_code == 201

    response = await auth_client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "registration_unavailable", "message": "无法使用该邮箱注册"}
    }


@pytest.mark.asyncio
async def test_login_failure_does_not_reveal_account_existence(
    auth_client: AsyncClient,
) -> None:
    await auth_client.post(
        "/api/v1/auth/register",
        json={"email": "known@example.com", "password": "correct horse battery staple"},
    )

    wrong_password = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "known@example.com", "password": "incorrect password"},
    )
    unknown_account = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@example.com", "password": "incorrect password"},
    )

    assert wrong_password.status_code == 401
    assert unknown_account.status_code == 401
    assert wrong_password.json() == unknown_account.json() == {
        "detail": {"code": "invalid_credentials", "message": "邮箱或密码错误"}
    }


@pytest.mark.asyncio
async def test_auth_rate_limit_has_stable_api_error() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=RejectAllRateLimiter(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "limited@example.com", "password": "correct horse battery staple"},
        )

    assert response.status_code == 429
    assert response.json() == {
        "detail": {"code": "rate_limit_exceeded", "message": "请求过于频繁，请稍后重试"}
    }
    await engine.dispose()
