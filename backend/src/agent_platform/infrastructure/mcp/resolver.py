from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

from agent_platform.infrastructure.mcp.client import MCPClient, PythonSDKMCPClient
from agent_platform.infrastructure.mcp.models import (
    MCPServerConfig,
    MCPStdioConfig,
    MCPStreamableHTTPConfig,
)
from agent_platform.platform.tools.entities import McpServer, McpTransport


class MCPServerReader(Protocol):
    async def get_server(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
    ) -> McpServer | None: ...


class MCPClientBuilder(Protocol):
    def __call__(self, config: MCPServerConfig) -> MCPClient: ...


@runtime_checkable
class StdioExecutionPolicy(Protocol):
    async def allows(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        command: str,
        args: tuple[str, ...],
    ) -> bool: ...


class DenyStdioExecutionPolicy:
    @staticmethod
    async def allows(
        *,
        tenant_id: UUID,
        server_id: UUID,
        command: str,
        args: tuple[str, ...],
    ) -> bool:
        del tenant_id, server_id, command, args
        return False


class MCPClientResolutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MCPServerUnavailableError(MCPClientResolutionError):
    def __init__(self) -> None:
        super().__init__("mcp_server_unavailable", "MCP server is unavailable")


class MCPStdioExecutionDeniedError(MCPClientResolutionError):
    def __init__(self) -> None:
        super().__init__("mcp_stdio_execution_denied", "MCP stdio execution is denied")


class MCPServerConfigurationError(MCPClientResolutionError):
    def __init__(self) -> None:
        super().__init__("mcp_server_invalid_config", "MCP server configuration is invalid")


class DatabaseMCPClientResolver:
    def __init__(
        self,
        server_reader: MCPServerReader,
        *,
        client_builder: MCPClientBuilder | None = None,
        stdio_policy: StdioExecutionPolicy | None = None,
    ) -> None:
        self._server_reader = server_reader
        self._client_builder = client_builder or PythonSDKMCPClient
        self._stdio_policy = stdio_policy or DenyStdioExecutionPolicy()

    async def resolve(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        credentials: Mapping[str, str],
    ) -> MCPClient:
        server = await self._server_reader.get_server(
            tenant_id=tenant_id,
            server_id=server_id,
        )
        if server is None or not server.enabled:
            raise MCPServerUnavailableError

        config = await self._config_for(
            server=server,
            tenant_id=tenant_id,
            credentials=credentials,
        )
        try:
            return self._client_builder(config)
        except Exception:
            raise MCPServerConfigurationError from None

    async def _config_for(
        self,
        *,
        server: McpServer,
        tenant_id: UUID,
        credentials: Mapping[str, str],
    ) -> MCPServerConfig:
        if server.transport is McpTransport.STREAMABLE_HTTP:
            if not server.endpoint:
                raise MCPServerConfigurationError
            return MCPStreamableHTTPConfig(
                url=_validated_http_endpoint(server.endpoint),
                headers=dict(credentials),
            )

        if not server.command:
            raise MCPServerConfigurationError
        args = tuple(server.args)
        try:
            allowed = await self._stdio_policy.allows(
                tenant_id=tenant_id,
                server_id=server.id,
                command=server.command,
                args=args,
            )
        except Exception:
            raise MCPStdioExecutionDeniedError from None
        if not allowed:
            raise MCPStdioExecutionDeniedError
        return MCPStdioConfig(
            command=server.command,
            args=args,
            env=dict(credentials),
        )


def _validated_http_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        _ = parsed.port
    except ValueError:
        raise MCPServerConfigurationError from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise MCPServerConfigurationError
    return endpoint
