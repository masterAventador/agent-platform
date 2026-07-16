"""C09 真实边界验收：生命周期 API × 真实 MCP stub（HTTP + 官方协议栈）。

覆盖恶意（畸形/未授权）、超慢 Server 的 fail-closed 语义，以及凭据只在
执行边界短时解析、响应不回显明文的契约。
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.mcp.probe import MCPCatalogProbe
from agent_platform.infrastructure.secrets import (
    LocalFileCredentialResolver,
    LocalFileCredentialStore,
)

SECRET_TOKEN = "stub-secret-token-do-not-echo"


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def stub_server() -> AsyncIterator[str]:
    # FastMCP 的 StreamableHTTPSessionManager 仅允许 run 一次，
    # 因此 stub 在模块级启动一次，各用例通过控制端点重置状态。
    from tests.fixtures.mcp_stub import app as stub_app

    config = uvicorn.Config(stub_app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        server.should_exit = True
        await task


@pytest_asyncio.fixture(loop_scope="module")
async def reset_stub(stub_server: str) -> str:
    async with httpx.AsyncClient() as control:
        await control.post(f"{stub_server}/__control/profile", json={"profile": "v1"})
        await control.post(f"{stub_server}/__control/mode", json={"mode": "normal"})
        await control.post(f"{stub_server}/__control/auth", json={"token": None})
    return stub_server


@pytest_asyncio.fixture(loop_scope="module")
async def api_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    credentials_file = tmp_path / "secrets" / "credentials.json"
    repository_root = tmp_path / "repo"
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        auth_rate_limiter=AllowAllRateLimiter(),
        mcp_connection_probe=MCPCatalogProbe(timeout_seconds=2.0),
        tool_credential_store=LocalFileCredentialStore(
            credentials_file=credentials_file, repository_root=repository_root
        ),
        tool_credential_resolver=LocalFileCredentialResolver(
            credentials_file=credentials_file, repository_root=repository_root
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await engine.dispose()


async def _register_workspace(client: AsyncClient, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    return {"X-Tenant-ID": tenant_id}


async def _create_server(client: AsyncClient, headers: dict[str, str], endpoint: str) -> dict:
    response = await client.post(
        "/api/v1/mcp-servers",
        headers=headers,
        json={
            "name": "stub-mcp",
            "transport": "streamable_http",
            "endpoint": f"{endpoint}/mcp",
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio(loop_scope="module")
async def test_connection_test_and_sync_against_real_stub(
    reset_stub: str, api_client: AsyncClient
) -> None:
    stub_server = reset_stub
    headers = await _register_workspace(api_client, "real-stub@example.com")
    server = await _create_server(api_client, headers, stub_server)

    tested = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert tested.status_code == 200
    assert tested.json()["status"] == "ok"
    assert tested.json()["tool_count"] == 2

    synced = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/sync", headers=headers
    )
    assert synced.status_code == 200
    assert sorted(synced.json()["added"]) == ["search_customers", "send_notification"]

    async with httpx.AsyncClient() as control:
        await control.post(f"{stub_server}/__control/profile", json={"profile": "v2"})
    second = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/sync", headers=headers
    )
    body = second.json()
    assert body["added"] == ["fetch_order"]
    assert body["updated"] == ["search_customers"]
    assert body["removed"] == [{"name": "send_notification", "referenced": False}]


@pytest.mark.asyncio(loop_scope="module")
async def test_malicious_slow_and_malformed_stub_fail_closed(
    reset_stub: str, api_client: AsyncClient
) -> None:
    stub_server = reset_stub
    headers = await _register_workspace(api_client, "malicious-stub@example.com")
    server = await _create_server(api_client, headers, stub_server)

    async with httpx.AsyncClient() as control:
        await control.post(
            f"{stub_server}/__control/mode",
            json={"mode": "slow", "slow_seconds": 10.0},
        )
    slow = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert slow.status_code == 200
    assert slow.json()["status"] == "failed"
    assert slow.json()["error_code"] in {"mcp_timeout", "mcp_remote_error"}

    async with httpx.AsyncClient() as control:
        await control.post(f"{stub_server}/__control/mode", json={"mode": "malformed"})
    malformed = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/sync", headers=headers
    )
    assert malformed.status_code == 502
    assert malformed.json()["detail"]["code"] in {"mcp_remote_error", "mcp_timeout"}
    assert "not a valid MCP payload" not in malformed.text

    async with httpx.AsyncClient() as control:
        await control.post(f"{stub_server}/__control/mode", json={"mode": "normal"})
    recovered = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert recovered.json()["status"] == "ok"


@pytest.mark.asyncio(loop_scope="module")
async def test_credentials_resolve_only_at_probe_time_and_never_echo(
    reset_stub: str, api_client: AsyncClient
) -> None:
    stub_server = reset_stub
    headers = await _register_workspace(api_client, "credential-stub@example.com")
    server = await _create_server(api_client, headers, stub_server)

    async with httpx.AsyncClient() as control:
        await control.post(
            f"{stub_server}/__control/auth", json={"token": SECRET_TOKEN}
        )

    denied = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert denied.json()["status"] == "failed"

    configured = await api_client.put(
        f"/api/v1/mcp-servers/{server['id']}/credentials",
        headers=headers,
        json={"values": {"Authorization": f"Bearer {SECRET_TOKEN}"}},
    )
    assert configured.status_code == 200
    assert SECRET_TOKEN not in configured.text

    allowed = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert allowed.json()["status"] == "ok"
    assert SECRET_TOKEN not in allowed.text

    listed = await api_client.get("/api/v1/mcp-servers", headers=headers)
    assert SECRET_TOKEN not in listed.text

    removed = await api_client.delete(
        f"/api/v1/mcp-servers/{server['id']}/credentials", headers=headers
    )
    assert removed.json()["has_credentials"] is False
    denied_again = await api_client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert denied_again.json()["status"] == "failed"
