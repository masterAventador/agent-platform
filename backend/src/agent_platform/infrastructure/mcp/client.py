import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from typing import Any, Protocol, cast, runtime_checkable
from uuid import UUID

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import JsonValue, TypeAdapter

from agent_platform.infrastructure.mcp.errors import (
    MCPClientError,
    MCPRemoteError,
    MCPTimeoutError,
    MCPToolExecutionError,
)
from agent_platform.infrastructure.mcp.models import (
    MCPServerConfig,
    MCPStreamableHTTPConfig,
    MCPTool,
)

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
type _HTTPTransport = Callable[
    ...,
    AbstractAsyncContextManager[tuple[Any, Any, Any]],
]
type _StdioTransport = Callable[
    ...,
    AbstractAsyncContextManager[tuple[Any, Any]],
]
type _SessionConstructor = Callable[..., AbstractAsyncContextManager[Any]]


class MCPSession(Protocol):
    async def initialize(self) -> object: ...

    async def list_tools(self, cursor: str | None) -> types.ListToolsResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> types.CallToolResult: ...


class MCPSessionFactory(Protocol):
    def __call__(self, config: MCPServerConfig) -> AbstractAsyncContextManager[MCPSession]: ...


@runtime_checkable
class MCPClient(Protocol):
    async def list_tools(self) -> list[MCPTool]: ...

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        invocation_id: UUID | None = None,
    ) -> JsonValue: ...


class PythonSDKSessionFactory:
    def __init__(
        self,
        *,
        http_transport: _HTTPTransport | None = None,
        stdio_transport: _StdioTransport | None = None,
        session_constructor: _SessionConstructor | None = None,
    ) -> None:
        self._http_transport = http_transport or cast(_HTTPTransport, streamable_http_client)
        self._stdio_transport = stdio_transport or cast(_StdioTransport, stdio_client)
        self._session_constructor = session_constructor or cast(
            _SessionConstructor,
            ClientSession,
        )

    @asynccontextmanager
    async def __call__(self, config: MCPServerConfig) -> AsyncIterator[MCPSession]:
        timeout = timedelta(seconds=config.timeout_seconds)
        if isinstance(config, MCPStreamableHTTPConfig):
            async with (
                httpx.AsyncClient(
                    headers=dict(config.headers),
                    timeout=config.timeout_seconds,
                ) as http_client,
                self._http_transport(
                    config.url,
                    http_client=http_client,
                ) as (read_stream, write_stream, _),
                self._session_constructor(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                ) as session,
            ):
                yield cast(MCPSession, session)
            return

        server = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            env=dict(config.env) if config.env is not None else None,
        )
        async with (
            self._stdio_transport(server) as (read_stream, write_stream),
            self._session_constructor(
                read_stream,
                write_stream,
                read_timeout_seconds=timeout,
            ) as session,
        ):
            yield cast(MCPSession, session)


class PythonSDKMCPClient:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        session_factory: MCPSessionFactory | None = None,
    ) -> None:
        self._config = config
        self._session_factory = session_factory or PythonSDKSessionFactory()

    async def list_tools(self) -> list[MCPTool]:
        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                async with self._session_factory(self._config) as session:
                    await session.initialize()
                    return await self._list_all_tools(session)
        except TimeoutError:
            raise MCPTimeoutError() from None
        except MCPClientError:
            raise
        except Exception:
            raise MCPRemoteError() from None

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        invocation_id: UUID | None = None,
    ) -> JsonValue:
        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                async with self._session_factory(self._config) as session:
                    await session.initialize()
                    metadata = (
                        {"io.agent-platform/invocation-id": str(invocation_id)}
                        if invocation_id is not None
                        else None
                    )
                    result = await session.call_tool(
                        name,
                        dict(arguments),
                        meta=metadata,
                    )
                    if result.isError:
                        raise MCPToolExecutionError()
                    return self._normalize_result(result)
        except TimeoutError:
            raise MCPTimeoutError() from None
        except MCPClientError:
            raise
        except Exception:
            raise MCPRemoteError() from None

    @staticmethod
    async def _list_all_tools(session: MCPSession) -> list[MCPTool]:
        tools: list[MCPTool] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            result = await session.list_tools(cursor)
            tools.extend(PythonSDKMCPClient._normalize_tool(tool) for tool in result.tools)
            cursor = result.nextCursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise MCPRemoteError()
            seen_cursors.add(cursor)

    @staticmethod
    def _normalize_tool(tool: types.Tool) -> MCPTool:
        output_schema = (
            _JSON_OBJECT_ADAPTER.validate_python(tool.outputSchema)
            if tool.outputSchema is not None
            else None
        )
        return MCPTool(
            name=tool.name,
            title=tool.title,
            description=tool.description,
            input_schema=_JSON_OBJECT_ADAPTER.validate_python(tool.inputSchema),
            output_schema=output_schema,
        )

    @staticmethod
    def _normalize_result(result: types.CallToolResult) -> JsonValue:
        if result.structuredContent is not None:
            normalized: JsonValue = _JSON_VALUE_ADAPTER.validate_python(
                result.structuredContent
            )
            return normalized
        content = [
            block.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
                exclude={"annotations", "meta"},
            )
            for block in result.content
        ]
        normalized_content: JsonValue = _JSON_VALUE_ADAPTER.validate_python(content)
        return normalized_content
