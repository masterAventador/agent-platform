"""C09 MCP 目录探测适配器：错误映射、stdio 策略与凭据传递。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_platform.infrastructure.mcp.errors import MCPRemoteError, MCPTimeoutError
from agent_platform.infrastructure.mcp.models import (
    MCPServerConfig,
    MCPStdioConfig,
    MCPStreamableHTTPConfig,
    MCPTool,
)
from agent_platform.infrastructure.mcp.probe import MCPCatalogProbe
from agent_platform.infrastructure.mcp.resolver import AllowlistStdioExecutionPolicy
from agent_platform.platform.tools.entities import McpServer, McpTransport
from agent_platform.platform.tools.errors import McpConnectionFailed

TENANT = uuid4()


def _server(**overrides) -> McpServer:
    values = {
        "tenant_id": TENANT,
        "created_by": uuid4(),
        "name": "probe-target",
        "transport": McpTransport.STREAMABLE_HTTP,
        "endpoint": "https://mcp.example.com/api",
        "command": None,
        "args": [],
        "secret_reference": None,
        "enabled": True,
    }
    values.update(overrides)
    return McpServer.create(**values)


class FakeClient:
    def __init__(self, *, tools=None, error=None) -> None:
        self._tools = tools or []
        self._error = error

    async def list_tools(self):
        if self._error is not None:
            raise self._error
        return self._tools

    async def call_tool(self, name, arguments, *, invocation_id=None):
        raise AssertionError("probe must not call tools")


class RecordingBuilder:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.configs: list[MCPServerConfig] = []

    def __call__(self, config: MCPServerConfig) -> FakeClient:
        self.configs.append(config)
        return self.client


@pytest.mark.asyncio
async def test_probe_lists_catalog_with_credentials_as_headers() -> None:
    builder = RecordingBuilder(
        FakeClient(
            tools=[
                MCPTool(
                    name="search",
                    description="desc",
                    input_schema={"type": "object"},
                )
            ]
        )
    )
    probe = MCPCatalogProbe(timeout_seconds=5.0, client_builder=builder)

    catalog = await probe.list_tools(
        server=_server(), credentials={"Authorization": "Bearer x"}
    )

    assert [item.name for item in catalog] == ["search"]
    assert catalog[0].description == "desc"
    [config] = builder.configs
    assert isinstance(config, MCPStreamableHTTPConfig)
    assert config.headers == {"Authorization": "Bearer x"}
    assert config.timeout_seconds == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (MCPTimeoutError(), "mcp_timeout"),
        (MCPRemoteError(), "mcp_remote_error"),
        (RuntimeError("raw upstream text with secrets"), "mcp_remote_error"),
    ],
)
async def test_probe_maps_failures_to_stable_codes(error, expected_code) -> None:
    builder = RecordingBuilder(FakeClient(error=error))
    probe = MCPCatalogProbe(timeout_seconds=5.0, client_builder=builder)

    with pytest.raises(McpConnectionFailed) as failure:
        await probe.list_tools(server=_server(), credentials={})

    assert failure.value.code == expected_code
    assert "secrets" not in str(failure.value)


@pytest.mark.asyncio
async def test_probe_denies_stdio_without_allowlist() -> None:
    builder = RecordingBuilder(FakeClient())
    probe = MCPCatalogProbe(timeout_seconds=5.0, client_builder=builder)

    with pytest.raises(McpConnectionFailed) as failure:
        await probe.list_tools(
            server=_server(
                transport=McpTransport.STDIO,
                endpoint=None,
                command="uvx",
                args=["some-mcp"],
            ),
            credentials={},
        )

    assert failure.value.code == "mcp_stdio_execution_denied"
    assert builder.configs == []


@pytest.mark.asyncio
async def test_probe_allows_stdio_command_on_allowlist_with_credentials_as_env() -> None:
    builder = RecordingBuilder(FakeClient(tools=[]))
    probe = MCPCatalogProbe(
        timeout_seconds=5.0,
        client_builder=builder,
        stdio_policy=AllowlistStdioExecutionPolicy(["uvx"]),
    )

    catalog = await probe.list_tools(
        server=_server(
            transport=McpTransport.STDIO,
            endpoint=None,
            command="uvx",
            args=["some-mcp"],
        ),
        credentials={"API_KEY": "k"},
    )

    assert catalog == []
    [config] = builder.configs
    assert isinstance(config, MCPStdioConfig)
    assert config.env == {"API_KEY": "k"}


@pytest.mark.asyncio
async def test_probe_rejects_invalid_endpoint_configuration() -> None:
    builder = RecordingBuilder(FakeClient())
    probe = MCPCatalogProbe(timeout_seconds=5.0, client_builder=builder)

    with pytest.raises(McpConnectionFailed) as failure:
        await probe.list_tools(
            server=_server(endpoint="https://user:pass@mcp.example.com/api"),
            credentials={},
        )

    assert failure.value.code == "mcp_server_invalid_config"
