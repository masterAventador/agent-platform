from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def tool_client() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _register_workspace(client: AsyncClient, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    return {"X-Tenant-ID": tenant_id}


@pytest.mark.asyncio
async def test_mcp_server_and_tool_registry_contract(tool_client) -> None:
    tool_client, _ = tool_client
    headers = await _register_workspace(tool_client, "tool-owner@example.com")
    created_server = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=headers,
        json={
            "name": "orders-mcp",
            "transport": "streamable_http",
            "endpoint": "https://mcp.internal.example/orders",
            "enabled": True,
            "secret_reference": "vault://tenant/orders-mcp",
        },
    )
    assert created_server.status_code == 201
    server = created_server.json()
    assert server["transport"] == "streamable_http"
    assert server["has_credentials"] is True
    assert "secret_reference" not in server
    assert "vault://tenant/orders-mcp" not in created_server.text
    assert (await tool_client.get("/api/v1/mcp-servers", headers=headers)).json() == [server]

    disabled_server = await tool_client.patch(
        f"/api/v1/mcp-servers/{server['id']}",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled_server.status_code == 200
    assert disabled_server.json()["enabled"] is False

    created_tool = await tool_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "server_id": server["id"],
            "name": "search_orders",
            "description": "Search orders",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            "risk_level": "read",
            "enabled": True,
        },
    )
    assert created_tool.status_code == 201
    tool = created_tool.json()
    assert tool["server_id"] == server["id"]
    assert tool["risk_level"] == "read"
    assert (await tool_client.get("/api/v1/tools", headers=headers)).json() == [tool]

    disabled = await tool_client.patch(
        f"/api/v1/tools/{tool['id']}", headers=headers, json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


@pytest.mark.asyncio
async def test_transport_configuration_validation_and_tenant_isolation(
    tool_client,
) -> None:
    tool_client, _ = tool_client
    first_headers = await _register_workspace(tool_client, "first-tool-owner@example.com")
    invalid = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=first_headers,
        json={"name": "invalid", "transport": "stdio", "endpoint": "https://example.com"},
    )
    assert invalid.status_code == 422
    credential_in_url = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=first_headers,
        json={
            "name": "leaky-url",
            "transport": "streamable_http",
            "endpoint": "https://token@example.com/mcp",
        },
    )
    assert credential_in_url.status_code == 422
    credential_in_query = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=first_headers,
        json={
            "name": "leaky-query",
            "transport": "streamable_http",
            "endpoint": "https://example.com/mcp?api_key=plaintext",
        },
    )
    assert credential_in_query.status_code == 422
    plaintext_reference = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=first_headers,
        json={
            "name": "plaintext-secret",
            "transport": "stdio",
            "command": "uvx",
            "secret_reference": "plaintext-password",
        },
    )
    assert plaintext_reference.status_code == 422
    oversized_arg = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=first_headers,
        json={
            "name": "oversized-arg",
            "transport": "stdio",
            "command": "uvx",
            "args": ["x" * 501],
        },
    )
    assert oversized_arg.status_code == 422

    created = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=first_headers,
        json={"name": "local-tools", "transport": "stdio", "command": "uvx"},
    )
    assert created.status_code == 201
    server_id = created.json()["id"]
    await tool_client.post("/api/v1/auth/logout")
    second_headers = await _register_workspace(tool_client, "second-tool-owner@example.com")

    assert (await tool_client.get("/api/v1/mcp-servers", headers=second_headers)).json() == []
    cross_tenant_tool = await tool_client.post(
        "/api/v1/tools",
        headers=second_headers,
        json={
            "server_id": server_id,
            "name": "hidden_tool",
            "description": "Must stay isolated",
            "input_schema": {"type": "object"},
            "risk_level": "read",
        },
    )
    assert cross_tenant_tool.status_code == 404
    cross_tenant_patch = await tool_client.patch(
        f"/api/v1/mcp-servers/{server_id}",
        headers=second_headers,
        json={"enabled": False},
    )
    assert cross_tenant_patch.status_code == 404


@pytest.mark.asyncio
async def test_employee_can_only_bind_enabled_tools_from_enabled_tenant_server(
    tool_client,
) -> None:
    tool_client, _ = tool_client
    headers = await _register_workspace(tool_client, "tool-binding-owner@example.com")
    server_response = await tool_client.post(
        "/api/v1/mcp-servers",
        headers=headers,
        json={
            "name": "employee-tools",
            "transport": "streamable_http",
            "endpoint": "https://mcp.internal.example/employee-tools",
        },
    )
    server_id = server_response.json()["id"]
    tool_response = await tool_client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "server_id": server_id,
            "name": "lookup_customer",
            "description": "Look up a customer",
            "input_schema": {"type": "object"},
            "risk_level": "read",
            "enabled": False,
        },
    )
    tool_id = tool_response.json()["id"]
    definition = {
        "name": "客户服务专员",
        "role_description": "查询客户资料并提供服务",
        "work_mode": "autonomous",
        "system_prompt": "只使用已授权工具处理客户请求。",
        "model": {"provider": "openai", "name": "gpt-5"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": True,
            "scheduled_tasks": False,
            "file_upload": False,
        },
        "tool_ids": [tool_id],
    }

    disabled_tool = await tool_client.post(
        "/api/v1/employees", headers=headers, json=definition
    )
    assert disabled_tool.status_code == 422
    assert disabled_tool.json()["detail"]["code"] == "tool_not_bindable"

    await tool_client.patch(f"/api/v1/tools/{tool_id}", headers=headers, json={"enabled": True})
    bound = await tool_client.post(
        "/api/v1/employees",
        headers=headers,
        json={**definition, "name": "已授权客户服务专员"},
    )
    assert bound.status_code == 201
    assert bound.json()["definition"]["tool_ids"] == [tool_id]

    await tool_client.patch(
        f"/api/v1/mcp-servers/{server_id}", headers=headers, json={"enabled": False}
    )
    disabled_server = await tool_client.post(
        "/api/v1/employees",
        headers=headers,
        json={**definition, "name": "服务已禁用的专员"},
    )
    assert disabled_server.status_code == 422
    assert disabled_server.json()["detail"]["code"] == "tool_not_bindable"

    await tool_client.patch(
        f"/api/v1/mcp-servers/{server_id}", headers=headers, json={"enabled": True}
    )
    await tool_client.post("/api/v1/auth/logout")
    other_headers = await _register_workspace(tool_client, "other-tool-binding@example.com")
    cross_tenant = await tool_client.post(
        "/api/v1/employees",
        headers=other_headers,
        json={**definition, "name": "其他企业客户服务专员"},
    )
    assert cross_tenant.status_code == 422
    assert cross_tenant.json()["detail"]["code"] == "tool_not_bindable"


@pytest.mark.asyncio
async def test_member_can_read_registry_but_only_owner_can_write(tool_client) -> None:
    client, session_factory = tool_client
    owner_headers = await _register_workspace(client, "registry-owner@example.com")
    tenant_id = UUID(owner_headers["X-Tenant-ID"])
    created = await client.post(
        "/api/v1/mcp-servers",
        headers=owner_headers,
        json={"name": "member-visible", "transport": "stdio", "command": "uvx"},
    )
    assert created.status_code == 201
    await client.post("/api/v1/auth/logout")
    await _register_workspace(client, "registry-member@example.com")
    member = (await client.get("/api/v1/auth/me")).json()
    member_id = UUID(member["id"])
    async with session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=member_id,
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    member_headers = {"X-Tenant-ID": str(tenant_id)}

    listed = await client.get("/api/v1/mcp-servers", headers=member_headers)
    assert listed.status_code == 200
    assert [server["name"] for server in listed.json()] == ["member-visible"]
    forbidden = await client.post(
        "/api/v1/mcp-servers",
        headers=member_headers,
        json={"name": "member-write", "transport": "stdio", "command": "uvx"},
    )
    assert forbidden.status_code == 403
