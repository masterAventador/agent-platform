"""C09 Tool/MCP 生命周期领域服务单元测试。"""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.tools.entities import (
    DiscoveredTool,
    McpConnectionStatus,
    McpServer,
    McpSyncReport,
    McpTransport,
    Tool,
    ToolApprovalPolicy,
    ToolOrigin,
    ToolReference,
    ToolRiskLevel,
    ToolVersion,
)
from agent_platform.platform.tools.errors import (
    InvalidApprovalPolicy,
    InvalidToolSchema,
    McpConnectionFailed,
    McpServerInUse,
    McpServerNotFound,
    ToolInUse,
    ToolNotFound,
    ToolVersionNotFound,
)
from agent_platform.platform.tools.services import ToolRegistryService

TENANT = uuid4()
ACTOR = uuid4()


class InMemoryToolRepository:
    def __init__(self) -> None:
        self.servers: dict[tuple[UUID, UUID], McpServer] = {}
        self.tools: dict[tuple[UUID, UUID], Tool] = {}
        self.versions: list[ToolVersion] = []
        self.reports: list[McpSyncReport] = []
        self.references: list[ToolReference] = []
        self.locked_servers: list[UUID] = []

    async def add_server(self, server: McpServer) -> None:
        self.servers[(server.tenant_id, server.id)] = server

    async def get_server(self, *, tenant_id, server_id):
        return self.servers.get((tenant_id, server_id))

    async def get_server_for_update(self, *, tenant_id, server_id):
        self.locked_servers.append(server_id)
        return self.servers.get((tenant_id, server_id))

    async def list_servers(self, *, tenant_id):
        return [server for (owner, _), server in self.servers.items() if owner == tenant_id]

    async def update_server(self, server: McpServer) -> None:
        self.servers[(server.tenant_id, server.id)] = server

    async def delete_server(self, *, tenant_id, server_id) -> None:
        self.servers.pop((tenant_id, server_id), None)
        for key, tool in list(self.tools.items()):
            if tool.server_id == server_id:
                del self.tools[key]

    async def add_tool(self, tool: Tool) -> None:
        self.tools[(tool.tenant_id, tool.id)] = tool

    async def get_tool(self, *, tenant_id, tool_id):
        return self.tools.get((tenant_id, tool_id))

    async def list_tools(self, *, tenant_id, server_id=None):
        return [
            tool
            for (owner, _), tool in self.tools.items()
            if owner == tenant_id and (server_id is None or tool.server_id == server_id)
        ]

    async def update_tool(self, tool: Tool) -> None:
        self.tools[(tool.tenant_id, tool.id)] = tool

    async def delete_tool(self, *, tenant_id, tool_id) -> None:
        self.tools.pop((tenant_id, tool_id), None)
        self.versions = [item for item in self.versions if item.tool_id != tool_id]

    async def add_tool_version(self, version: ToolVersion) -> None:
        self.versions.append(version)

    async def list_tool_versions(self, *, tenant_id, tool_id):
        return sorted(
            (
                item
                for item in self.versions
                if item.tenant_id == tenant_id and item.tool_id == tool_id
            ),
            key=lambda item: item.version,
        )

    async def get_tool_version(self, *, tenant_id, tool_id, version):
        for item in self.versions:
            if (
                item.tenant_id == tenant_id
                and item.tool_id == tool_id
                and item.version == version
            ):
                return item
        return None

    async def list_tool_references(self, *, tenant_id, tool_ids):
        del tenant_id
        wanted = set(tool_ids)
        return [item for item in self.references if item.tool_id in wanted]

    async def add_sync_report(self, report: McpSyncReport, *, keep: int) -> None:
        self.reports.append(report)
        matching = [item for item in self.reports if item.server_id == report.server_id]
        for stale in matching[:-keep]:
            self.reports.remove(stale)

    async def list_sync_reports(self, *, tenant_id, server_id, limit):
        return [
            item
            for item in reversed(self.reports)
            if item.tenant_id == tenant_id and item.server_id == server_id
        ][:limit]


class StaticProbe:
    def __init__(
        self,
        tools: list[DiscoveredTool] | None = None,
        error: McpConnectionFailed | None = None,
    ) -> None:
        self.tools = tools or []
        self.error = error
        self.calls: list[Mapping[str, str]] = []

    async def list_tools(self, *, server, credentials):
        del server
        self.calls.append(credentials)
        if self.error is not None:
            raise self.error
        return self.tools


class StaticCredentials:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def resolve(self, *, tenant_id, references):
        del tenant_id
        if self.fail:
            raise RuntimeError("secret backend down: token=super-secret")
        return {"Authorization": "Bearer resolved"} if references else {}


def _service(
    repository: InMemoryToolRepository,
    probe: StaticProbe | None = None,
    credentials: StaticCredentials | None = None,
) -> ToolRegistryService:
    return ToolRegistryService(
        repository,
        connection_probe=probe,
        credential_resolver=credentials or StaticCredentials(),
    )


async def _server(service: ToolRegistryService, **overrides) -> McpServer:
    values = {
        "tenant_id": TENANT,
        "created_by": ACTOR,
        "name": "search-mcp",
        "transport": McpTransport.STREAMABLE_HTTP,
        "endpoint": "https://mcp.example.com/api",
        "command": None,
        "args": [],
        "secret_reference": None,
        "enabled": True,
    }
    values.update(overrides)
    return await service.register_server(**values)


async def _tool(service: ToolRegistryService, server: McpServer, **overrides) -> Tool:
    values = {
        "tenant_id": TENANT,
        "server_id": server.id,
        "name": "search",
        "description": "search things",
        "input_schema": {"type": "object"},
        "risk_level": ToolRiskLevel.READ,
        "enabled": True,
    }
    values.update(overrides)
    return await service.register_tool(**values)


@pytest.mark.asyncio
async def test_manual_tool_registration_records_initial_version() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)

    assert tool.version == 1
    assert tool.origin is ToolOrigin.MANUAL
    assert tool.approval_policy is ToolApprovalPolicy.RISK_BASED
    versions = await service.list_tool_versions(tenant_id=TENANT, tool_id=tool.id)
    assert [item.version for item in versions] == [1]
    assert versions[0].change_source == "initial"


@pytest.mark.asyncio
async def test_update_tool_bumps_version_and_snapshots_definition() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)

    updated = await service.update_tool(
        tenant_id=TENANT,
        tool_id=tool.id,
        description="更新后的说明",
        risk_level=ToolRiskLevel.WRITE,
        approval_policy=ToolApprovalPolicy.ALWAYS,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    assert updated.version == 2
    assert updated.risk_level is ToolRiskLevel.WRITE
    assert updated.approval_policy is ToolApprovalPolicy.ALWAYS
    versions = await service.list_tool_versions(tenant_id=TENANT, tool_id=tool.id)
    assert [item.version for item in versions] == [1, 2]
    assert versions[1].change_source == "update"
    assert versions[0].risk_level is ToolRiskLevel.READ


@pytest.mark.asyncio
async def test_enabled_only_toggle_does_not_create_version() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)

    toggled = await service.update_tool(tenant_id=TENANT, tool_id=tool.id, enabled=False)

    assert toggled.enabled is False
    assert toggled.version == 1
    versions = await service.list_tool_versions(tenant_id=TENANT, tool_id=tool.id)
    assert [item.version for item in versions] == [1]


@pytest.mark.asyncio
async def test_destructive_risk_rejects_never_approval_policy() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)

    with pytest.raises(InvalidApprovalPolicy):
        await service.update_tool(
            tenant_id=TENANT,
            tool_id=tool.id,
            risk_level=ToolRiskLevel.DESTRUCTIVE,
            approval_policy=ToolApprovalPolicy.NEVER,
        )
    with pytest.raises(InvalidApprovalPolicy):
        await service.register_tool(
            tenant_id=TENANT,
            server_id=server.id,
            name="wipe",
            description="",
            input_schema={"type": "object"},
            risk_level=ToolRiskLevel.DESTRUCTIVE,
            approval_policy=ToolApprovalPolicy.NEVER,
            enabled=True,
        )


@pytest.mark.asyncio
async def test_oversized_or_non_object_schema_rejected() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)

    with pytest.raises(InvalidToolSchema):
        await _tool(service, server, input_schema={"type": "string"})
    with pytest.raises(InvalidToolSchema):
        await _tool(
            service,
            server,
            name="big",
            input_schema={"type": "object", "blob": "x" * (64 * 1024 + 1)},
        )


@pytest.mark.asyncio
async def test_rollback_restores_old_definition_as_new_version() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)
    await service.update_tool(
        tenant_id=TENANT,
        tool_id=tool.id,
        description="v2",
        risk_level=ToolRiskLevel.EXTERNAL,
    )

    restored = await service.rollback_tool(tenant_id=TENANT, tool_id=tool.id, version=1)

    assert restored.version == 3
    assert restored.description == "search things"
    assert restored.risk_level is ToolRiskLevel.READ
    versions = await service.list_tool_versions(tenant_id=TENANT, tool_id=tool.id)
    assert [item.version for item in versions] == [1, 2, 3]
    assert versions[2].change_source == "rollback"

    with pytest.raises(ToolVersionNotFound):
        await service.rollback_tool(tenant_id=TENANT, tool_id=tool.id, version=99)


@pytest.mark.asyncio
async def test_delete_tool_protected_while_referenced() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)
    repository.references.append(
        ToolReference(
            tool_id=tool.id,
            employee_id=uuid4(),
            employee_name="研究员",
            relation="employee_draft",
            version=None,
        )
    )

    with pytest.raises(ToolInUse):
        await service.delete_tool(tenant_id=TENANT, tool_id=tool.id)

    repository.references.clear()
    await service.delete_tool(tenant_id=TENANT, tool_id=tool.id)
    with pytest.raises(ToolNotFound):
        await service.delete_tool(tenant_id=TENANT, tool_id=tool.id)


@pytest.mark.asyncio
async def test_delete_server_protected_while_any_tool_referenced() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)
    repository.references.append(
        ToolReference(
            tool_id=tool.id,
            employee_id=uuid4(),
            employee_name="研究员",
            relation="employee_version",
            version=3,
        )
    )

    with pytest.raises(McpServerInUse):
        await service.delete_server(tenant_id=TENANT, server_id=server.id)

    repository.references.clear()
    await service.delete_server(tenant_id=TENANT, server_id=server.id)
    with pytest.raises(McpServerNotFound):
        await service.delete_server(tenant_id=TENANT, server_id=server.id)
    assert await service.list_tools(tenant_id=TENANT) == []


@pytest.mark.asyncio
async def test_update_server_settings_keeps_transport_and_bumps_updated_at() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)

    updated = await service.update_server(
        tenant_id=TENANT,
        server_id=server.id,
        name="renamed-mcp",
        endpoint="https://mcp.example.com/v2",
    )

    assert updated.name == "renamed-mcp"
    assert updated.endpoint == "https://mcp.example.com/v2"
    assert updated.transport is McpTransport.STREAMABLE_HTTP
    assert updated.updated_at >= server.updated_at


@pytest.mark.asyncio
async def test_connection_test_success_persists_status() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(
        tools=[DiscoveredTool(name="a", description="", input_schema={"type": "object"})]
    )
    service = _service(repository, probe=probe)
    server = await _server(service, secret_reference="local://mcp-servers/x")

    result = await service.test_server_connection(tenant_id=TENANT, server_id=server.id)

    assert result.status is McpConnectionStatus.OK
    assert result.tool_count == 1
    stored = await service.get_server(tenant_id=TENANT, server_id=server.id)
    assert stored.connection_status is McpConnectionStatus.OK
    assert stored.connection_error_code is None
    assert stored.connection_tested_at is not None
    assert probe.calls == [{"Authorization": "Bearer resolved"}]


@pytest.mark.asyncio
async def test_connection_test_failure_persists_stable_code_only() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(error=McpConnectionFailed("mcp_timeout"))
    service = _service(repository, probe=probe)
    server = await _server(service)

    result = await service.test_server_connection(tenant_id=TENANT, server_id=server.id)

    assert result.status is McpConnectionStatus.FAILED
    assert result.error_code == "mcp_timeout"
    stored = await service.get_server(tenant_id=TENANT, server_id=server.id)
    assert stored.connection_status is McpConnectionStatus.FAILED
    assert stored.connection_error_code == "mcp_timeout"


@pytest.mark.asyncio
async def test_connection_test_credential_failure_is_sanitized() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe()
    service = _service(repository, probe=probe, credentials=StaticCredentials(fail=True))
    server = await _server(service, secret_reference="local://mcp-servers/x")

    result = await service.test_server_connection(tenant_id=TENANT, server_id=server.id)

    assert result.status is McpConnectionStatus.FAILED
    assert result.error_code == "credential_unavailable"
    assert "super-secret" not in (result.error_code or "")
    assert probe.calls == []


@pytest.mark.asyncio
async def test_sync_creates_discovered_tools_fail_closed_defaults() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(
        tools=[
            DiscoveredTool(
                name="search",
                description="upstream search",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            )
        ]
    )
    service = _service(repository, probe=probe)
    server = await _server(service)

    report = await service.sync_server(tenant_id=TENANT, server_id=server.id)

    assert report.added == ["search"]
    assert report.updated == []
    assert report.removed == []
    assert report.status == "ok"
    [tool] = await service.list_tools(tenant_id=TENANT, server_id=server.id)
    assert tool.origin is ToolOrigin.DISCOVERED
    assert tool.enabled is False
    assert tool.risk_level is ToolRiskLevel.EXTERNAL
    assert tool.version == 1
    assert repository.locked_servers == [server.id]
    stored = await service.get_server(tenant_id=TENANT, server_id=server.id)
    assert stored.last_synced_at is not None
    assert stored.connection_status is McpConnectionStatus.OK


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_reports_updates_and_removals() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(
        tools=[
            DiscoveredTool(name="search", description="v1", input_schema={"type": "object"}),
            DiscoveredTool(name="fetch", description="v1", input_schema={"type": "object"}),
        ]
    )
    service = _service(repository, probe=probe)
    server = await _server(service)
    await service.sync_server(tenant_id=TENANT, server_id=server.id)

    second = await service.sync_server(tenant_id=TENANT, server_id=server.id)
    assert (second.added, second.updated, second.removed) == ([], [], [])

    tools = {tool.name: tool for tool in await service.list_tools(tenant_id=TENANT)}
    await service.update_tool(tenant_id=TENANT, tool_id=tools["search"].id, enabled=True)
    repository.references.append(
        ToolReference(
            tool_id=tools["fetch"].id,
            employee_id=uuid4(),
            employee_name="研究员",
            relation="employee_draft",
            version=None,
        )
    )

    probe.tools = [
        DiscoveredTool(
            name="search",
            description="v2",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
    ]
    third = await service.sync_server(tenant_id=TENANT, server_id=server.id)

    assert third.added == []
    assert third.updated == ["search"]
    assert [(entry.name, entry.referenced) for entry in third.removed] == [("fetch", True)]

    tools = {tool.name: tool for tool in await service.list_tools(tenant_id=TENANT)}
    assert tools["search"].description == "v2"
    assert tools["search"].version == 2
    assert tools["search"].enabled is True  # 管理员运维状态不被同步覆盖
    assert tools["fetch"].upstream_missing is True
    versions = await service.list_tool_versions(tenant_id=TENANT, tool_id=tools["search"].id)
    assert versions[-1].change_source == "sync"

    # 上游恢复后解除 missing 标记
    probe.tools.append(
        DiscoveredTool(name="fetch", description="v1", input_schema={"type": "object"})
    )
    fourth = await service.sync_server(tenant_id=TENANT, server_id=server.id)
    assert fourth.updated == ["fetch"]
    tools = {tool.name: tool for tool in await service.list_tools(tenant_id=TENANT)}
    assert tools["fetch"].upstream_missing is False


@pytest.mark.asyncio
async def test_sync_failure_records_failed_report_and_status() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(error=McpConnectionFailed("mcp_unreachable"))
    service = _service(repository, probe=probe)
    server = await _server(service)

    with pytest.raises(McpConnectionFailed) as failure:
        await service.sync_server(tenant_id=TENANT, server_id=server.id)

    assert failure.value.code == "mcp_unreachable"
    reports = await service.list_sync_reports(tenant_id=TENANT, server_id=server.id, limit=5)
    assert [item.status for item in reports] == ["failed"]
    assert reports[0].error_code == "mcp_unreachable"
    stored = await service.get_server(tenant_id=TENANT, server_id=server.id)
    assert stored.connection_status is McpConnectionStatus.FAILED


@pytest.mark.asyncio
async def test_sync_rejects_malformed_or_excessive_upstream_catalog() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(
        tools=[
            DiscoveredTool(name="bad", description="", input_schema={"type": "string"}),
        ]
    )
    service = _service(repository, probe=probe)
    server = await _server(service)

    with pytest.raises(McpConnectionFailed) as invalid:
        await service.sync_server(tenant_id=TENANT, server_id=server.id)
    assert invalid.value.code == "mcp_invalid_catalog"
    assert await service.list_tools(tenant_id=TENANT) == []

    probe.tools = [
        DiscoveredTool(name=f"tool-{index}", description="", input_schema={"type": "object"})
        for index in range(201)
    ]
    with pytest.raises(McpConnectionFailed) as excessive:
        await service.sync_server(tenant_id=TENANT, server_id=server.id)
    assert excessive.value.code == "mcp_catalog_too_large"


@pytest.mark.asyncio
async def test_sync_reports_are_pruned_per_server() -> None:
    repository = InMemoryToolRepository()
    probe = StaticProbe(tools=[])
    service = _service(repository, probe=probe)
    server = await _server(service)

    for _ in range(25):
        await service.sync_server(tenant_id=TENANT, server_id=server.id)

    reports = await service.list_sync_reports(tenant_id=TENANT, server_id=server.id, limit=100)
    assert len(reports) == 20


@pytest.mark.asyncio
async def test_required_available_tool_rejects_upstream_missing() -> None:
    repository = InMemoryToolRepository()
    service = _service(repository)
    server = await _server(service)
    tool = await _tool(service, server)
    stored = repository.tools[(TENANT, tool.id)]
    repository.tools[(TENANT, tool.id)] = stored.mark_upstream_missing(
        missing=True, at=datetime.now(UTC)
    )

    with pytest.raises(ToolNotFound):
        await service.required_available_tool(tenant_id=TENANT, tool_id=tool.id)
