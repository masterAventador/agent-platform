from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from agent_platform.platform.tools.entities import (
    DiscoveredTool,
    McpServer,
    McpSyncReport,
    Tool,
    ToolReference,
    ToolVersion,
)


class ToolRepository(Protocol):
    async def add_server(self, server: McpServer) -> None: ...

    async def get_server(self, *, tenant_id: UUID, server_id: UUID) -> McpServer | None: ...

    async def get_server_for_update(
        self, *, tenant_id: UUID, server_id: UUID
    ) -> McpServer | None: ...

    async def list_servers(self, *, tenant_id: UUID) -> list[McpServer]: ...

    async def update_server(self, server: McpServer) -> None: ...

    async def delete_server(self, *, tenant_id: UUID, server_id: UUID) -> None: ...

    async def add_tool(self, tool: Tool) -> None: ...

    async def get_tool(self, *, tenant_id: UUID, tool_id: UUID) -> Tool | None: ...

    async def list_tools(
        self, *, tenant_id: UUID, server_id: UUID | None = None
    ) -> list[Tool]: ...

    async def update_tool(self, tool: Tool) -> None: ...

    async def delete_tool(self, *, tenant_id: UUID, tool_id: UUID) -> None: ...

    async def add_tool_version(self, version: ToolVersion) -> None: ...

    async def list_tool_versions(
        self, *, tenant_id: UUID, tool_id: UUID
    ) -> list[ToolVersion]: ...

    async def get_tool_version(
        self, *, tenant_id: UUID, tool_id: UUID, version: int
    ) -> ToolVersion | None: ...

    async def list_tool_references(
        self, *, tenant_id: UUID, tool_ids: Sequence[UUID]
    ) -> list[ToolReference]: ...

    async def add_sync_report(self, report: McpSyncReport, *, keep: int) -> None: ...

    async def list_sync_reports(
        self, *, tenant_id: UUID, server_id: UUID, limit: int
    ) -> list[McpSyncReport]: ...


class McpConnectionProbe(Protocol):
    """Sanitized MCP catalog probe; raises McpConnectionFailed with stable codes."""

    async def list_tools(
        self, *, server: McpServer, credentials: Mapping[str, str]
    ) -> list[DiscoveredTool]: ...


class ToolCredentialResolver(Protocol):
    async def resolve(
        self, *, tenant_id: UUID, references: Sequence[str]
    ) -> Mapping[str, str]: ...
