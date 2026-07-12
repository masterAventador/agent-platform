from uuid import UUID

from agent_platform.platform.tools.entities import (
    McpServer,
    McpTransport,
    Tool,
    ToolRiskLevel,
)
from agent_platform.platform.tools.errors import McpServerNotFound, ToolNotFound
from agent_platform.platform.tools.ports import ToolRepository


class ToolRegistryService:
    def __init__(self, repository: ToolRepository) -> None:
        self._repository = repository

    async def register_server(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        transport: McpTransport,
        endpoint: str | None,
        command: str | None,
        args: list[str],
        secret_reference: str | None,
        enabled: bool,
    ) -> McpServer:
        server = McpServer.create(
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            transport=transport,
            endpoint=endpoint,
            command=command,
            args=args,
            secret_reference=secret_reference,
            enabled=enabled,
        )
        await self._repository.add_server(server)
        return server

    async def list_servers(self, *, tenant_id: UUID) -> list[McpServer]:
        return await self._repository.list_servers(tenant_id=tenant_id)

    async def set_server_enabled(
        self, *, tenant_id: UUID, server_id: UUID, enabled: bool
    ) -> McpServer:
        server = await self._repository.get_server(tenant_id=tenant_id, server_id=server_id)
        if server is None:
            raise McpServerNotFound
        updated = server.set_enabled(enabled)
        await self._repository.update_server(updated)
        return updated

    async def register_tool(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        name: str,
        description: str,
        input_schema: dict[str, object],
        risk_level: ToolRiskLevel,
        enabled: bool,
    ) -> Tool:
        if await self._repository.get_server(tenant_id=tenant_id, server_id=server_id) is None:
            raise McpServerNotFound
        tool = Tool.create(
            tenant_id=tenant_id,
            server_id=server_id,
            name=name,
            description=description,
            input_schema=input_schema,
            risk_level=risk_level,
            enabled=enabled,
        )
        await self._repository.add_tool(tool)
        return tool

    async def list_tools(
        self, *, tenant_id: UUID, server_id: UUID | None = None
    ) -> list[Tool]:
        return await self._repository.list_tools(tenant_id=tenant_id, server_id=server_id)

    async def set_tool_enabled(
        self, *, tenant_id: UUID, tool_id: UUID, enabled: bool
    ) -> Tool:
        tool = await self._repository.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if tool is None:
            raise ToolNotFound
        updated = tool.set_enabled(enabled)
        await self._repository.update_tool(updated)
        return updated

    async def required_available_tool(self, *, tenant_id: UUID, tool_id: UUID) -> Tool:
        tool = await self._repository.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if tool is None or not tool.enabled:
            raise ToolNotFound
        server = await self._repository.get_server(
            tenant_id=tenant_id, server_id=tool.server_id
        )
        if server is None or not server.enabled:
            raise ToolNotFound
        return tool
