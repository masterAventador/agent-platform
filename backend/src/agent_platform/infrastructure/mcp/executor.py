from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from agent_platform.infrastructure.mcp.client import MCPClient
from agent_platform.infrastructure.mcp.errors import MCPToolExecutionError
from agent_platform.platform.tool_gateway.models import ToolDefinition

_JSON_ARGUMENTS_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(
    dict[str, JsonValue]
)


class MCPClientResolver(Protocol):
    async def resolve(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        credentials: Mapping[str, str],
    ) -> MCPClient: ...


class MCPToolExecutor:
    def __init__(self, client_resolver: MCPClientResolver) -> None:
        self._client_resolver = client_resolver

    async def execute(
        self,
        *,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        credentials: Mapping[str, str],
    ) -> object:
        normalized_arguments = _normalize_arguments(arguments)
        try:
            client = await self._client_resolver.resolve(
                tenant_id=definition.tenant_id,
                server_id=definition.server_id,
                credentials=credentials,
            )
            result = await client.call_tool(definition.name, normalized_arguments)
        except Exception:
            pass
        else:
            return result
        raise MCPToolExecutionError()


def _normalize_arguments(arguments: Mapping[str, object]) -> dict[str, JsonValue]:
    try:
        normalized = _JSON_ARGUMENTS_ADAPTER.validate_python(
            dict(arguments),
            strict=True,
        )
        json.dumps(normalized, allow_nan=False)
    except (TypeError, ValueError):
        pass
    else:
        return normalized
    raise MCPToolExecutionError()
