"""MCP 目录探测适配器：连接测试与工具自动发现共用的受限只读探针。

只调用 list_tools，绝不触发工具执行；所有失败都映射为携带稳定错误码的
`McpConnectionFailed`，不向上层泄露上游报文或异常文本。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_platform.infrastructure.mcp.client import MCPClient, PythonSDKMCPClient
from agent_platform.infrastructure.mcp.errors import (
    MCPRemoteError,
    MCPTimeoutError,
)
from agent_platform.infrastructure.mcp.models import MCPServerConfig
from agent_platform.infrastructure.mcp.resolver import (
    DenyStdioExecutionPolicy,
    MCPClientResolutionError,
    StdioExecutionPolicy,
    build_server_config,
)
from agent_platform.platform.tools.entities import DiscoveredTool, McpServer
from agent_platform.platform.tools.errors import McpConnectionFailed


class MCPClientBuilder(Protocol):
    def __call__(self, config: MCPServerConfig) -> MCPClient: ...


class MCPCatalogProbe:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        client_builder: MCPClientBuilder | None = None,
        stdio_policy: StdioExecutionPolicy | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._client_builder = client_builder or PythonSDKMCPClient
        self._stdio_policy = stdio_policy or DenyStdioExecutionPolicy()

    async def list_tools(
        self, *, server: McpServer, credentials: Mapping[str, str]
    ) -> list[DiscoveredTool]:
        try:
            config = await build_server_config(
                server=server,
                tenant_id=server.tenant_id,
                credentials=credentials,
                stdio_policy=self._stdio_policy,
                timeout_seconds=self._timeout_seconds,
            )
        except MCPClientResolutionError as failure:
            raise McpConnectionFailed(failure.code) from None

        try:
            client = self._client_builder(config)
            tools = await client.list_tools()
        except MCPTimeoutError:
            raise McpConnectionFailed("mcp_timeout") from None
        except MCPRemoteError:
            raise McpConnectionFailed("mcp_remote_error") from None
        except MCPClientResolutionError as failure:
            raise McpConnectionFailed(failure.code) from None
        except Exception:
            raise McpConnectionFailed("mcp_remote_error") from None
        return [
            DiscoveredTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.input_schema),
            )
            for tool in tools
        ]
