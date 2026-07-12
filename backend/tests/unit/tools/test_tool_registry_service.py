from uuid import uuid4

import pytest

from agent_platform.platform.tools.entities import (
    McpServer,
    McpTransport,
    Tool,
    ToolRiskLevel,
)
from agent_platform.platform.tools.errors import McpServerNotFound, ToolNotFound
from agent_platform.platform.tools.services import ToolRegistryService


class InMemoryToolRepository:
    def __init__(self) -> None:
        self.servers: dict[tuple[object, object], McpServer] = {}
        self.tools: dict[tuple[object, object], Tool] = {}

    async def add_server(self, server: McpServer) -> None:
        self.servers[(server.tenant_id, server.id)] = server

    async def get_server(self, *, tenant_id, server_id):
        return self.servers.get((tenant_id, server_id))

    async def list_servers(self, *, tenant_id):
        return [server for (owner, _), server in self.servers.items() if owner == tenant_id]

    async def update_server(self, server: McpServer) -> None:
        self.servers[(server.tenant_id, server.id)] = server

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


@pytest.mark.asyncio
async def test_registers_server_and_discovered_tool_without_remote_execution() -> None:
    repository = InMemoryToolRepository()
    service = ToolRegistryService(repository)
    tenant_id = uuid4()
    actor_id = uuid4()

    server = await service.register_server(
        tenant_id=tenant_id,
        created_by=actor_id,
        name="internal-http",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.internal.example/api",
        command=None,
        args=[],
        secret_reference="vault://tenants/acme/mcp/internal",
        enabled=True,
    )
    tool = await service.register_tool(
        tenant_id=tenant_id,
        server_id=server.id,
        name="search_orders",
        description="Search tenant orders",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        risk_level=ToolRiskLevel.READ,
        enabled=True,
    )

    assert server.secret_reference == "vault://tenants/acme/mcp/internal"
    assert tool.server_id == server.id
    assert await service.list_tools(tenant_id=tenant_id, server_id=server.id) == [tool]


@pytest.mark.asyncio
async def test_tenant_scope_hides_servers_tools_and_controls_tool_enablement() -> None:
    repository = InMemoryToolRepository()
    service = ToolRegistryService(repository)
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    server = await service.register_server(
        tenant_id=tenant_id,
        created_by=uuid4(),
        name="local-stdio",
        transport=McpTransport.STDIO,
        endpoint=None,
        command="uvx",
        args=["mcp-server-sqlite"],
        secret_reference=None,
        enabled=True,
    )
    tool = await service.register_tool(
        tenant_id=tenant_id,
        server_id=server.id,
        name="query_database",
        description="Run a read-only query",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.DESTRUCTIVE,
        enabled=True,
    )

    disabled = await service.set_tool_enabled(
        tenant_id=tenant_id, tool_id=tool.id, enabled=False
    )
    assert disabled.enabled is False
    assert await service.list_servers(tenant_id=other_tenant_id) == []
    assert await service.list_tools(tenant_id=other_tenant_id) == []
    with pytest.raises(McpServerNotFound):
        await service.register_tool(
            tenant_id=other_tenant_id,
            server_id=server.id,
            name="cross_tenant",
            description="Must fail",
            input_schema={"type": "object"},
            risk_level=ToolRiskLevel.READ,
            enabled=True,
        )
    with pytest.raises(ToolNotFound):
        await service.set_tool_enabled(
            tenant_id=other_tenant_id, tool_id=tool.id, enabled=False
        )


@pytest.mark.asyncio
async def test_tool_is_unavailable_when_its_server_is_disabled() -> None:
    repository = InMemoryToolRepository()
    service = ToolRegistryService(repository)
    tenant_id = uuid4()
    server = await service.register_server(
        tenant_id=tenant_id,
        created_by=uuid4(),
        name="disabled-server",
        transport=McpTransport.STDIO,
        endpoint=None,
        command="uvx",
        args=[],
        secret_reference=None,
        enabled=False,
    )
    tool = await service.register_tool(
        tenant_id=tenant_id,
        server_id=server.id,
        name="otherwise-enabled",
        description="Must inherit the server availability",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.EXTERNAL,
        enabled=True,
    )

    with pytest.raises(ToolNotFound):
        await service.required_available_tool(tenant_id=tenant_id, tool_id=tool.id)

    enabled_server = await service.set_server_enabled(
        tenant_id=tenant_id, server_id=server.id, enabled=True
    )
    assert enabled_server.enabled is True
    assert await service.required_available_tool(tenant_id=tenant_id, tool_id=tool.id) == tool

    with pytest.raises(McpServerNotFound):
        await service.set_server_enabled(
            tenant_id=uuid4(), server_id=server.id, enabled=True
        )
