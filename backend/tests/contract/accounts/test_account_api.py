"""C15 账号体系 API 契约测试。

覆盖：资料查看/编辑、修改密码（校验旧密码 + 撤销其它会话）、邮箱验证 token、
找回密码闭环与防用户枚举、会话/设备列表与撤销（撤销后旧会话立即失效）。
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models

PASSWORD = "correct horse battery staple"


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def api() -> AsyncIterator[tuple[FastAPI, async_sessionmaker, ASGITransport]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False, expose_dev_account_tokens=True),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield app, session_factory, ASGITransport(app=app)
    await engine.dispose()


async def _register_and_login(
    transport: ASGITransport, email: str
) -> tuple[AsyncClient, dict[str, Any]]:
    client = AsyncClient(transport=transport, base_url="http://testserver")
    credentials = {"email": email, "password": PASSWORD}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    me = (await client.get("/api/v1/auth/me")).json()
    return client, me


@pytest.mark.asyncio
async def test_view_and_edit_profile(api) -> None:
    _, _, transport = api
    client, _ = await _register_and_login(transport, "user@example.com")

    profile = await client.get("/api/v1/account/profile")
    assert profile.status_code == 200
    assert profile.json()["email"] == "user@example.com"
    assert profile.json()["display_name"] is None

    updated = await client.patch(
        "/api/v1/account/profile", json={"display_name": "  张三  "}
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "张三"

    assert (await client.get("/api/v1/account/profile")).json()["display_name"] == "张三"


@pytest.mark.asyncio
async def test_change_password_requires_correct_current_password(api) -> None:
    _, _, transport = api
    client, _ = await _register_and_login(transport, "user@example.com")

    wrong = await client.post(
        "/api/v1/account/password",
        json={"current_password": "wrong-password-value", "new_password": "another good password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_current_password"


@pytest.mark.asyncio
async def test_change_password_updates_and_revokes_other_sessions(api) -> None:
    _, _, transport = api
    client, _ = await _register_and_login(transport, "user@example.com")
    # a second, older session for the same user
    other = AsyncClient(transport=transport, base_url="http://testserver")
    assert (
        await other.post(
            "/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}
        )
    ).status_code == 200
    assert (await other.get("/api/v1/auth/me")).status_code == 200

    new_password = "brand new correct passphrase"
    change = await client.post(
        "/api/v1/account/password",
        json={"current_password": PASSWORD, "new_password": new_password},
    )
    assert change.status_code == 204

    # current session stays valid; the other session is revoked immediately
    assert (await client.get("/api/v1/account/profile")).status_code == 200
    assert (await other.get("/api/v1/auth/me")).status_code == 401

    # login works with the new password only
    fresh = AsyncClient(transport=transport, base_url="http://testserver")
    assert (
        await fresh.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": new_password},
        )
    ).status_code == 200
    assert (
        await fresh.post(
            "/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_email_verification_flow(api) -> None:
    _, _, transport = api
    client, me = await _register_and_login(transport, "user@example.com")
    assert me["email_verified"] is False

    request = await client.post("/api/v1/account/email-verification/request")
    assert request.status_code == 200
    token = request.json()["token"]
    assert token

    confirm = await client.post(
        "/api/v1/account/email-verification/confirm", json={"token": token}
    )
    assert confirm.status_code == 204
    assert (await client.get("/api/v1/account/profile")).json()["email_verified"] is True

    # token cannot be replayed
    replay = await client.post(
        "/api/v1/account/email-verification/confirm", json={"token": token}
    )
    assert replay.status_code == 400
    assert replay.json()["detail"]["code"] == "token_invalid_or_expired"


@pytest.mark.asyncio
async def test_password_reset_does_not_reveal_account_existence(api) -> None:
    _, _, transport = api
    await _register_and_login(transport, "known@example.com")
    anonymous = AsyncClient(transport=transport, base_url="http://testserver")

    known = await anonymous.post(
        "/api/v1/account/password-reset/request", json={"email": "known@example.com"}
    )
    unknown = await anonymous.post(
        "/api/v1/account/password-reset/request", json={"email": "nobody@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert "token" not in known.json()


@pytest.mark.asyncio
async def test_password_reset_full_loop_and_session_revocation(api) -> None:
    _, _, transport = api
    client, _ = await _register_and_login(transport, "user@example.com")
    anonymous = AsyncClient(transport=transport, base_url="http://testserver")

    reset_request = await anonymous.post(
        "/api/v1/account/password-reset/request", json={"email": "user@example.com"}
    )
    assert reset_request.status_code == 202

    dev_token = await anonymous.get(
        "/api/v1/account/password-reset/dev-token", params={"email": "user@example.com"}
    )
    assert dev_token.status_code == 200
    token = dev_token.json()["token"]

    new_password = "recovered stronger passphrase"
    confirm = await anonymous.post(
        "/api/v1/account/password-reset/confirm",
        json={"token": token, "new_password": new_password},
    )
    assert confirm.status_code == 204

    # existing session invalidated after reset
    assert (await client.get("/api/v1/account/profile")).status_code == 401
    # login with new password works
    fresh = AsyncClient(transport=transport, base_url="http://testserver")
    assert (
        await fresh.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": new_password},
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_dev_token_absent_for_unknown_email(api) -> None:
    _, _, transport = api
    anonymous = AsyncClient(transport=transport, base_url="http://testserver")
    response = await anonymous.get(
        "/api/v1/account/password-reset/dev-token", params={"email": "nobody@example.com"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dev_token_channel_disabled_returns_404() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False, expose_dev_account_tokens=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")
    credentials = {"email": "user@example.com", "password": PASSWORD}
    await client.post("/api/v1/auth/register", json=credentials)
    await client.post(
        "/api/v1/account/password-reset/request", json={"email": "user@example.com"}
    )
    response = await client.get(
        "/api/v1/account/password-reset/dev-token", params={"email": "user@example.com"}
    )
    assert response.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_and_revoke_sessions(api) -> None:
    _, _, transport = api
    client, _ = await _register_and_login(transport, "user@example.com")
    other = AsyncClient(transport=transport, base_url="http://testserver")
    assert (
        await other.post(
            "/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}
        )
    ).status_code == 200

    sessions = await client.get("/api/v1/account/sessions")
    assert sessions.status_code == 200
    payload = sessions.json()
    assert len(payload) == 2
    current = [s for s in payload if s["current"]]
    assert len(current) == 1

    other_session = next(s for s in payload if not s["current"])
    revoke = await client.request(
        "DELETE", f"/api/v1/account/sessions/{other_session['id']}"
    )
    assert revoke.status_code == 204
    assert (await other.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/account/profile")).status_code == 200


@pytest.mark.asyncio
async def test_revoke_all_other_sessions_keeps_current(api) -> None:
    _, _, transport = api
    client, _ = await _register_and_login(transport, "user@example.com")
    other = AsyncClient(transport=transport, base_url="http://testserver")
    assert (
        await other.post(
            "/api/v1/auth/login", json={"email": "user@example.com", "password": PASSWORD}
        )
    ).status_code == 200

    revoke_all = await client.request("DELETE", "/api/v1/account/sessions")
    assert revoke_all.status_code == 204
    assert (await other.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/account/profile")).status_code == 200


@pytest.mark.asyncio
async def test_account_endpoints_require_authentication(api) -> None:
    _, _, transport = api
    anonymous = AsyncClient(transport=transport, base_url="http://testserver")
    assert (await anonymous.get("/api/v1/account/profile")).status_code == 401
    assert (await anonymous.get("/api/v1/account/sessions")).status_code == 401


@pytest.mark.asyncio
async def test_password_reset_request_is_rate_limited() -> None:
    """找回密码请求端点的防枚举主防线之一是限流：超限统一返回 429。"""
    from agent_platform.platform.auth.errors import RateLimitExceeded

    class RejectPasswordResetRateLimiter:
        async def ensure_allowed(self, *, scope: str, key: str) -> None:
            del key
            if scope.startswith("password_reset"):
                raise RateLimitExceeded

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=RejectPasswordResetRateLimiter(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    anonymous = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    response = await anonymous.post(
        "/api/v1/account/password-reset/request", json={"email": "someone@example.com"}
    )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limit_exceeded"
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_operation_without_workspace_is_audited_or_logged(api, caplog) -> None:
    """无主工作区用户的账号管理操作不得静默丢失审计，至少落受控告警。"""
    import uuid

    from sqlalchemy import delete

    from agent_platform.infrastructure.database.repositories.tenants import (
        TenantMembershipRecord,
    )

    _, session_factory, transport = api
    client, me = await _register_and_login(transport, "orphan@example.com")

    async with session_factory() as session:
        await session.execute(
            delete(TenantMembershipRecord).where(
                TenantMembershipRecord.user_id == uuid.UUID(me["id"])
            )
        )
        await session.commit()

    with caplog.at_level("WARNING", logger="agent_platform.api.routes.account"):
        change = await client.post(
            "/api/v1/account/password",
            json={"current_password": PASSWORD, "new_password": "another good strong pass"},
        )
    assert change.status_code == 204
    assert "account_audit_skipped_no_workspace" in caplog.text
