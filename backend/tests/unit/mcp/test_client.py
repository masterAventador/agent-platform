import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import types

from agent_platform.infrastructure.mcp import (
    MCPClient,
    MCPRemoteError,
    MCPStdioConfig,
    MCPStreamableHTTPConfig,
    MCPTimeoutError,
    MCPToolExecutionError,
    PythonSDKMCPClient,
)


class FakeSession:
    def __init__(
        self,
        *,
        tool_pages: Mapping[str | None, types.ListToolsResult] | None = None,
        tool_result: types.CallToolResult | None = None,
        error: Exception | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self.tool_pages = tool_pages or {}
        self.tool_result = tool_result
        self.error = error
        self.delay_seconds = delay_seconds
        self.initialized = False
        self.list_cursors: list[str | None] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> object:
        self.initialized = True
        return object()

    async def list_tools(self, cursor: str | None = None) -> types.ListToolsResult:
        self.list_cursors.append(cursor)
        await self._wait_or_raise()
        return self.tool_pages[cursor]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> types.CallToolResult:
        self.calls.append((name, arguments or {}))
        await self._wait_or_raise()
        assert self.tool_result is not None
        return self.tool_result

    async def _wait_or_raise(self) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error


def session_factory_for(session: FakeSession):
    @asynccontextmanager
    async def factory(
        _config: MCPStreamableHTTPConfig | MCPStdioConfig,
    ) -> AsyncIterator[FakeSession]:
        yield session

    return factory


def test_http_config_repr_does_not_expose_headers() -> None:
    secret = "Bearer do-not-log"
    config = MCPStreamableHTTPConfig(
        url="https://mcp.example.test/mcp",
        headers={"Authorization": secret},
    )

    assert secret not in repr(config)


def test_stdio_config_repr_does_not_expose_environment() -> None:
    secret = "do-not-log"
    config = MCPStdioConfig(command="server", env={"API_TOKEN": secret})

    assert secret not in repr(config)


@pytest.mark.asyncio
async def test_list_tools_initializes_session_and_normalizes_all_pages() -> None:
    session = FakeSession(
        tool_pages={
            None: types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="search",
                        description="Search documents",
                        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
                    )
                ],
                nextCursor="page-2",
            ),
            "page-2": types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="summarize",
                        title="Summarize",
                        inputSchema={"type": "object"},
                        outputSchema={"type": "object"},
                    )
                ]
            ),
        }
    )
    client = PythonSDKMCPClient(
        MCPStdioConfig(command="python", args=("server.py",), env={"MODE": "test"}),
        session_factory=session_factory_for(session),
    )

    assert isinstance(client, MCPClient)
    tools = await client.list_tools()

    assert session.initialized is True
    assert session.list_cursors == [None, "page-2"]
    assert [tool.model_dump() for tool in tools] == [
        {
            "name": "search",
            "title": None,
            "description": "Search documents",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            "output_schema": None,
        },
        {
            "name": "summarize",
            "title": "Summarize",
            "description": None,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    ]


@pytest.mark.asyncio
async def test_call_tool_prefers_structured_content() -> None:
    session = FakeSession(
        tool_result=types.CallToolResult(
            content=[types.TextContent(type="text", text="human readable")],
            structuredContent={"count": 2, "items": ["a", "b"]},
        )
    )
    client = PythonSDKMCPClient(
        MCPStreamableHTTPConfig(
            url="https://mcp.example.test/mcp",
            headers={"Authorization": "Bearer secret"},
        ),
        session_factory=session_factory_for(session),
    )

    result = await client.call_tool("search", {"query": "invoice"})

    assert result == {"count": 2, "items": ["a", "b"]}
    assert session.calls == [("search", {"query": "invoice"})]


@pytest.mark.asyncio
async def test_call_tool_normalizes_unstructured_content_blocks() -> None:
    session = FakeSession(
        tool_result=types.CallToolResult(
            content=[
                types.TextContent(type="text", text="done"),
                types.ImageContent(type="image", data="base64", mimeType="image/png"),
            ]
        )
    )
    client = PythonSDKMCPClient(
        MCPStdioConfig(command="server"),
        session_factory=session_factory_for(session),
    )

    result = await client.call_tool("render", {})

    assert result == [
        {"type": "text", "text": "done"},
        {"type": "image", "data": "base64", "mimeType": "image/png"},
    ]


@pytest.mark.asyncio
async def test_tool_error_uses_stable_platform_error() -> None:
    session = FakeSession(
        tool_result=types.CallToolResult(
            content=[types.TextContent(type="text", text="database password leaked")],
            isError=True,
        )
    )
    client = PythonSDKMCPClient(
        MCPStdioConfig(command="server"),
        session_factory=session_factory_for(session),
    )

    with pytest.raises(MCPToolExecutionError) as captured:
        await client.call_tool("delete_records", {})

    assert captured.value.code == "mcp_tool_execution_failed"
    assert str(captured.value) == "MCP tool execution failed"
    assert "password" not in str(captured.value)


@pytest.mark.asyncio
async def test_timeout_uses_stable_platform_error() -> None:
    session = FakeSession(
        tool_pages={None: types.ListToolsResult(tools=[])},
        delay_seconds=0.05,
    )
    client = PythonSDKMCPClient(
        MCPStdioConfig(command="server", timeout_seconds=0.01),
        session_factory=session_factory_for(session),
    )

    with pytest.raises(MCPTimeoutError) as captured:
        await client.list_tools()

    assert captured.value.code == "mcp_timeout"
    assert str(captured.value) == "MCP request timed out"


@pytest.mark.asyncio
async def test_remote_exception_and_headers_are_not_exposed_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "Bearer do-not-log"
    session = FakeSession(
        tool_pages={None: types.ListToolsResult(tools=[])},
        error=RuntimeError(f"upstream failed with Authorization: {secret}"),
    )
    client = PythonSDKMCPClient(
        MCPStreamableHTTPConfig(
            url="https://mcp.example.test/mcp",
            headers={"Authorization": secret},
        ),
        session_factory=session_factory_for(session),
    )

    with pytest.raises(MCPRemoteError) as captured:
        await client.list_tools()

    assert captured.value.code == "mcp_remote_error"
    assert str(captured.value) == "MCP server request failed"
    assert secret not in str(captured.value)
    assert secret not in caplog.text
