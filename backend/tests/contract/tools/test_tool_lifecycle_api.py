"""C09 Tool/MCP 生命周期 API 契约测试。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import ToolAuditRecord
from agent_platform.infrastructure.database.repositories.employees import EmployeeRecord
from agent_platform.infrastructure.secrets import (
    LocalFileCredentialResolver,
    LocalFileCredentialStore,
)
from agent_platform.platform.tools.entities import DiscoveredTool
from agent_platform.platform.tools.errors import McpConnectionFailed


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class ProgrammableProbe:
    def __init__(self) -> None:
        self.tools: list[DiscoveredTool] = []
        self.error: McpConnectionFailed | None = None
        self.received_credentials: list[dict[str, str]] = []

    async def list_tools(self, *, server, credentials):
        del server
        self.received_credentials.append(dict(credentials))
        if self.error is not None:
            raise self.error
        return self.tools


@pytest_asyncio.fixture
async def lifecycle_client(tmp_path: Path) -> AsyncIterator[dict]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    probe = ProgrammableProbe()
    credentials_file = tmp_path / "secrets" / "credentials.json"
    repository_root = tmp_path / "repo"
    store = LocalFileCredentialStore(
        credentials_file=credentials_file, repository_root=repository_root
    )
    resolver = LocalFileCredentialResolver(
        credentials_file=credentials_file, repository_root=repository_root
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
        mcp_connection_probe=probe,
        tool_credential_store=store,
        tool_credential_resolver=resolver,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield {
            "client": client,
            "probe": probe,
            "session_factory": session_factory,
            "credentials_file": credentials_file,
        }
    await engine.dispose()


async def _register_workspace(client: AsyncClient, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    tenant_id = (await client.get("/api/v1/auth/me")).json()["workspaces"][0]["id"]
    return {"X-Tenant-ID": tenant_id}


async def _create_server(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    payload = {
        "name": "lifecycle-mcp",
        "transport": "streamable_http",
        "endpoint": "https://mcp.internal.example/api",
        "enabled": True,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/mcp-servers", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_server_edit_connection_test_and_delete(lifecycle_client) -> None:
    client, probe = lifecycle_client["client"], lifecycle_client["probe"]
    headers = await _register_workspace(client, "lifecycle-owner@example.com")
    server = await _create_server(client, headers)
    assert server["connection_status"] == "unknown"

    edited = await client.patch(
        f"/api/v1/mcp-servers/{server['id']}",
        headers=headers,
        json={"name": "renamed-mcp", "endpoint": "https://mcp.internal.example/v2"},
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "renamed-mcp"
    assert edited.json()["endpoint"] == "https://mcp.internal.example/v2"

    probe.tools = [
        DiscoveredTool(name="search", description="d", input_schema={"type": "object"})
    ]
    tested = await client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert tested.status_code == 200
    body = tested.json()
    assert body["status"] == "ok"
    assert body["tool_count"] == 1

    listed = await client.get("/api/v1/mcp-servers", headers=headers)
    assert listed.json()[0]["connection_status"] == "ok"

    probe.error = McpConnectionFailed("mcp_timeout")
    failed = await client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert failed.status_code == 200
    assert failed.json() == {
        "status": "failed",
        "tool_count": None,
        "error_code": "mcp_timeout",
        "tested_at": failed.json()["tested_at"],
    }

    deleted = await client.delete(
        f"/api/v1/mcp-servers/{server['id']}", headers=headers
    )
    assert deleted.status_code == 204
    assert (await client.get("/api/v1/mcp-servers", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_sync_discovers_tools_and_reports_diff(lifecycle_client) -> None:
    client, probe = lifecycle_client["client"], lifecycle_client["probe"]
    headers = await _register_workspace(client, "sync-owner@example.com")
    server = await _create_server(client, headers)

    probe.tools = [
        DiscoveredTool(name="search", description="v1", input_schema={"type": "object"}),
        DiscoveredTool(name="fetch", description="v1", input_schema={"type": "object"}),
    ]
    synced = await client.post(
        f"/api/v1/mcp-servers/{server['id']}/sync", headers=headers
    )
    assert synced.status_code == 200
    report = synced.json()
    assert sorted(report["added"]) == ["fetch", "search"]
    assert report["status"] == "ok"

    tools = (await client.get("/api/v1/tools", headers=headers)).json()
    assert {tool["name"] for tool in tools} == {"search", "fetch"}
    for tool in tools:
        assert tool["origin"] == "discovered"
        assert tool["enabled"] is False
        assert tool["risk_level"] == "external"
        assert tool["upstream_missing"] is False
        assert tool["version"] == 1

    probe.tools = [
        DiscoveredTool(name="search", description="v2", input_schema={"type": "object"}),
    ]
    second = await client.post(
        f"/api/v1/mcp-servers/{server['id']}/sync", headers=headers
    )
    body = second.json()
    assert body["updated"] == ["search"]
    assert body["removed"] == [{"name": "fetch", "referenced": False}]

    reports = await client.get(
        f"/api/v1/mcp-servers/{server['id']}/sync-reports", headers=headers
    )
    assert reports.status_code == 200
    assert len(reports.json()) == 2

    tools = {t["name"]: t for t in (await client.get("/api/v1/tools", headers=headers)).json()}
    assert tools["fetch"]["upstream_missing"] is True
    assert tools["search"]["version"] == 2

    probe.error = McpConnectionFailed("mcp_remote_error")
    failure = await client.post(
        f"/api/v1/mcp-servers/{server['id']}/sync", headers=headers
    )
    assert failure.status_code == 502
    assert failure.json()["detail"]["code"] == "mcp_remote_error"


@pytest.mark.asyncio
async def test_credentials_are_stored_and_never_echoed(lifecycle_client) -> None:
    client, probe = lifecycle_client["client"], lifecycle_client["probe"]
    headers = await _register_workspace(client, "credentials-owner@example.com")
    server = await _create_server(client, headers)
    secret_value = "Bearer secret-token-do-not-echo"

    configured = await client.put(
        f"/api/v1/mcp-servers/{server['id']}/credentials",
        headers=headers,
        json={"values": {"Authorization": secret_value}},
    )
    assert configured.status_code == 200
    assert configured.json()["has_credentials"] is True
    assert secret_value not in configured.text

    listed = await client.get("/api/v1/mcp-servers", headers=headers)
    assert secret_value not in listed.text
    assert listed.json()[0]["has_credentials"] is True

    probe.tools = []
    await client.post(
        f"/api/v1/mcp-servers/{server['id']}/connection-test", headers=headers
    )
    assert probe.received_credentials[-1] == {"Authorization": secret_value}

    audit = await client.get("/api/v1/audit/events", headers=headers)
    if audit.status_code == 200:
        assert secret_value not in audit.text

    removed = await client.delete(
        f"/api/v1/mcp-servers/{server['id']}/credentials", headers=headers
    )
    assert removed.status_code == 200
    assert removed.json()["has_credentials"] is False


@pytest.mark.asyncio
async def test_invalid_credential_payload_is_rejected(lifecycle_client) -> None:
    client = lifecycle_client["client"]
    headers = await _register_workspace(client, "credentials-invalid@example.com")
    server = await _create_server(client, headers)

    bad_key = await client.put(
        f"/api/v1/mcp-servers/{server['id']}/credentials",
        headers=headers,
        json={"values": {"Bad Key": "v"}},
    )
    assert bad_key.status_code == 422
    bad_value = await client.put(
        f"/api/v1/mcp-servers/{server['id']}/credentials",
        headers=headers,
        json={"values": {"Authorization": "a\r\nb"}},
    )
    assert bad_value.status_code == 422
    empty = await client.put(
        f"/api/v1/mcp-servers/{server['id']}/credentials",
        headers=headers,
        json={"values": {}},
    )
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_tool_update_rollback_versions_and_approval_validation(
    lifecycle_client,
) -> None:
    client = lifecycle_client["client"]
    headers = await _register_workspace(client, "tool-editor@example.com")
    server = await _create_server(client, headers)
    created = await client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "server_id": server["id"],
            "name": "search_customers",
            "description": "v1",
            "input_schema": {"type": "object"},
            "risk_level": "read",
            "enabled": True,
        },
    )
    assert created.status_code == 201
    tool = created.json()
    assert tool["version"] == 1
    assert tool["approval_policy"] == "risk_based"

    invalid = await client.patch(
        f"/api/v1/tools/{tool['id']}",
        headers=headers,
        json={"risk_level": "destructive", "approval_policy": "never"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "approval_policy_invalid"

    updated = await client.patch(
        f"/api/v1/tools/{tool['id']}",
        headers=headers,
        json={
            "description": "v2",
            "risk_level": "external",
            "approval_policy": "always",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    versions = await client.get(f"/api/v1/tools/{tool['id']}/versions", headers=headers)
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1, 2]
    assert versions.json()[1]["change_source"] == "update"

    rolled_back = await client.post(
        f"/api/v1/tools/{tool['id']}/rollback", headers=headers, json={"version": 1}
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["version"] == 3
    assert rolled_back.json()["description"] == "v1"
    assert rolled_back.json()["risk_level"] == "read"

    missing = await client.post(
        f"/api/v1/tools/{tool['id']}/rollback", headers=headers, json={"version": 42}
    )
    assert missing.status_code == 404

    bad_schema = await client.patch(
        f"/api/v1/tools/{tool['id']}",
        headers=headers,
        json={"input_schema": {"type": "string"}},
    )
    assert bad_schema.status_code == 422
    assert bad_schema.json()["detail"]["code"] == "tool_schema_invalid"


@pytest.mark.asyncio
async def test_tool_delete_reference_protection(lifecycle_client) -> None:
    client = lifecycle_client["client"]
    session_factory = lifecycle_client["session_factory"]
    headers = await _register_workspace(client, "tool-deleter@example.com")
    server = await _create_server(client, headers)
    created = await client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "server_id": server["id"],
            "name": "protected_tool",
            "description": "",
            "input_schema": {"type": "object"},
            "risk_level": "read",
            "enabled": True,
        },
    )
    tool = created.json()

    async with session_factory() as session:
        session.add(
            EmployeeRecord(
                id=uuid4(),
                tenant_id=UUID(headers["X-Tenant-ID"]),
                created_by=uuid4(),
                name="引用者",
                avatar_url=None,
                role_description="",
                visibility="tenant",
                runtime_type="autonomous",
                system_prompt="",
                model_settings={},
                input_schema={},
                output_schema={},
                capabilities={},
                skill_ids=[],
                tool_ids=[tool["id"]],
                knowledge_base_ids=[],
                approval_policy={},
                release_strategy={},
                status="draft",
                published_version=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    blocked = await client.delete(f"/api/v1/tools/{tool['id']}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "tool_in_use"
    references = blocked.json()["detail"]["references"]
    assert references[0]["employee_name"] == "引用者"

    blocked_server = await client.delete(
        f"/api/v1/mcp-servers/{server['id']}", headers=headers
    )
    assert blocked_server.status_code == 409
    assert blocked_server.json()["detail"]["code"] == "mcp_server_in_use"

    listed = await client.get(
        f"/api/v1/tools/{tool['id']}/references", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()[0]["relation"] == "employee_draft"

    async with session_factory() as session:
        from sqlalchemy import delete as sa_delete

        await session.execute(sa_delete(EmployeeRecord))
        await session.commit()

    assert (
        await client.delete(f"/api/v1/tools/{tool['id']}", headers=headers)
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/mcp-servers/{server['id']}", headers=headers)
    ).status_code == 204


@pytest.mark.asyncio
async def test_tool_invocations_listing_is_tenant_scoped(lifecycle_client) -> None:
    client = lifecycle_client["client"]
    session_factory = lifecycle_client["session_factory"]
    headers = await _register_workspace(client, "invocation-viewer@example.com")
    tenant_id = UUID(headers["X-Tenant-ID"])
    server = await _create_server(client, headers)
    created = await client.post(
        "/api/v1/tools",
        headers=headers,
        json={
            "server_id": server["id"],
            "name": "watched_tool",
            "description": "",
            "input_schema": {"type": "object"},
            "risk_level": "read",
            "enabled": True,
        },
    )
    tool = created.json()

    async with session_factory() as session:
        for index, (event_type, reason, succeeded) in enumerate(
            [
                ("tool.started", None, None),
                ("tool.completed", "tool_timeout", False),
                ("tool.rejected", "tool_disabled", None),
            ]
        ):
            session.add(
                ToolAuditRecord(
                    id=uuid4(),
                    event_type=event_type,
                    occurred_at=datetime(2026, 7, 16, 10, index, tzinfo=UTC),
                    tenant_id=tenant_id,
                    run_id=uuid4(),
                    employee_id=uuid4(),
                    user_id=uuid4(),
                    tool_id=UUID(tool["id"]),
                    tool_name=tool["name"],
                    risk="read",
                    argument_keys=["query"],
                    argument_sha256="0" * 64,
                    argument_size_bytes=10,
                    reason=reason,
                    succeeded=succeeded,
                    invocation_id=uuid4(),
                )
            )
        # 其他租户的记录不可见
        session.add(
            ToolAuditRecord(
                id=uuid4(),
                event_type="tool.completed",
                occurred_at=datetime(2026, 7, 16, 11, tzinfo=UTC),
                tenant_id=uuid4(),
                run_id=uuid4(),
                employee_id=uuid4(),
                user_id=uuid4(),
                tool_id=uuid4(),
                tool_name="foreign_tool",
                risk="read",
                argument_keys=[],
                argument_sha256="1" * 64,
                argument_size_bytes=2,
                reason=None,
                succeeded=True,
                invocation_id=uuid4(),
            )
        )
        await session.commit()

    listed = await client.get("/api/v1/tool-invocations", headers=headers)
    assert listed.status_code == 200
    events = listed.json()
    assert len(events) == 3
    assert events[0]["event_type"] == "tool.rejected"
    assert events[0]["reason"] == "tool_disabled"
    assert all(event["tool_name"] == "watched_tool" for event in events)

    filtered = await client.get(
        "/api/v1/tool-invocations",
        headers=headers,
        params={"tool_id": tool["id"], "limit": 1},
    )
    assert len(filtered.json()) == 1

    by_server = await client.get(
        "/api/v1/tool-invocations",
        headers=headers,
        params={"server_id": server["id"]},
    )
    assert len(by_server.json()) == 3
