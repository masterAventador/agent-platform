from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from agent_platform.infrastructure.mcp import (
    MCPStdioConfig,
    MCPStreamableHTTPConfig,
    PythonSDKSessionFactory,
)


class FakeClientSession:
    def __init__(self, read: object, write: object, **kwargs: Any) -> None:
        self.read = read
        self.write = write
        self.kwargs = kwargs

    async def __aenter__(self) -> "FakeClientSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_http_transport_passes_caller_headers_and_timeout() -> None:
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_http_transport(
        url: str,
        *,
        http_client: httpx.AsyncClient,
    ) -> AsyncIterator[tuple[object, object, object]]:
        captured["url"] = url
        captured["headers"] = dict(http_client.headers)
        captured["timeout"] = http_client.timeout
        yield object(), object(), lambda: None

    factory = PythonSDKSessionFactory(
        http_transport=fake_http_transport,
        session_constructor=FakeClientSession,
    )
    config = MCPStreamableHTTPConfig(
        url="https://mcp.example.test/mcp",
        headers={"X-Tenant-Token": "secret"},
        timeout_seconds=12.5,
    )

    async with factory(config) as session:
        assert isinstance(session, FakeClientSession)

    assert captured["url"] == config.url
    assert captured["headers"]["x-tenant-token"] == "secret"
    assert captured["timeout"].connect == 12.5
    assert session.kwargs["read_timeout_seconds"].total_seconds() == 12.5


@pytest.mark.asyncio
async def test_stdio_transport_passes_command_args_and_env() -> None:
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_stdio_transport(params: object) -> AsyncIterator[tuple[object, object]]:
        captured["params"] = params
        yield object(), object()

    factory = PythonSDKSessionFactory(
        stdio_transport=fake_stdio_transport,
        session_constructor=FakeClientSession,
    )
    config = MCPStdioConfig(
        command="uvx",
        args=("example-server", "--flag"),
        env={"API_TOKEN": "secret"},
        timeout_seconds=7,
    )

    async with factory(config) as session:
        assert isinstance(session, FakeClientSession)

    params = captured["params"]
    assert params.command == "uvx"
    assert params.args == ["example-server", "--flag"]
    assert params.env == {"API_TOKEN": "secret"}
    assert session.kwargs["read_timeout_seconds"].total_seconds() == 7
