from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from agent_platform.infrastructure.mcp import (
    MCPClient,
    MCPToolExecutionError,
    MCPToolExecutor,
)
from agent_platform.platform.tool_gateway import ToolDefinition, ToolExecutor, ToolRisk


@dataclass
class RecordingClient:
    result: JsonValue = None
    error: Exception | None = None
    calls: list[tuple[str, Mapping[str, JsonValue]]] = field(default_factory=list)

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> JsonValue:
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class RecordingClientResolver:
    client: MCPClient
    error: Exception | None = None
    calls: list[tuple[UUID, UUID, Mapping[str, str]]] = field(default_factory=list)

    async def resolve(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        credentials: Mapping[str, str],
    ) -> MCPClient:
        self.calls.append((tenant_id, server_id, credentials))
        if self.error is not None:
            raise self.error
        return self.client


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        tenant_id=uuid4(),
        tool_id=uuid4(),
        server_id=uuid4(),
        name="crm.lookup",
        risk=ToolRisk.READ,
    )


def accepts_tool_executor(executor: ToolExecutor) -> ToolExecutor:
    return executor


@pytest.mark.asyncio
async def test_execute_resolves_client_from_trusted_definition_and_calls_tool() -> None:
    definition = tool_definition()
    client = RecordingClient(result={"records": [1, 2]})
    resolver = RecordingClientResolver(client)
    executor = MCPToolExecutor(resolver)
    credentials = {"token": "resolved-secret"}
    arguments: Mapping[str, object] = {
        "customer": {"id": 42, "active": True},
        "fields": ["name", None],
    }

    result = await accepts_tool_executor(executor).execute(
        definition=definition,
        arguments=arguments,
        credentials=credentials,
    )

    assert result == {"records": [1, 2]}
    assert resolver.calls == [
        (definition.tenant_id, definition.server_id, credentials)
    ]
    assert client.calls == [(definition.name, arguments)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"payload": object()},
        {"items": (1, 2)},
        {"score": float("nan")},
        {"score": float("inf")},
    ],
)
async def test_invalid_json_arguments_fail_stably_before_client_resolution(
    arguments: Mapping[str, object],
) -> None:
    client = RecordingClient()
    resolver = RecordingClientResolver(client)
    executor = MCPToolExecutor(resolver)

    with pytest.raises(MCPToolExecutionError) as captured:
        await executor.execute(
            definition=tool_definition(),
            arguments=arguments,
            credentials={"token": "must-not-leak"},
        )

    assert captured.value.code == "mcp_tool_execution_failed"
    assert str(captured.value) == "MCP tool execution failed"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "must-not-leak" not in repr(captured.value)
    assert resolver.calls == []
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_source", ["resolver", "client"])
async def test_dependency_exceptions_and_credentials_are_not_exposed(
    failure_source: str,
) -> None:
    secret = "credential-must-remain-secret"
    remote_error = RuntimeError(f"upstream rejected {secret}")
    client = RecordingClient(error=remote_error if failure_source == "client" else None)
    resolver = RecordingClientResolver(
        client,
        error=remote_error if failure_source == "resolver" else None,
    )
    executor = MCPToolExecutor(resolver)

    with pytest.raises(MCPToolExecutionError) as captured:
        await executor.execute(
            definition=tool_definition(),
            arguments={"query": "customer"},
            credentials={"token": secret},
        )

    assert captured.value.code == "mcp_tool_execution_failed"
    assert str(captured.value) == "MCP tool execution failed"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert secret not in repr(captured.value)
