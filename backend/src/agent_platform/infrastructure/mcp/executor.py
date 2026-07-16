from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from agent_platform.infrastructure.mcp.client import MCPClient
from agent_platform.infrastructure.mcp.errors import (
    MCPClientError,
    MCPRemoteError,
    MCPTimeoutError,
    MCPToolExecutionError,
)
from agent_platform.infrastructure.mcp.resolver import MCPClientResolutionError
from agent_platform.platform.tool_gateway.errors import ToolExecutionFailure
from agent_platform.platform.tool_gateway.models import ToolDefinition, ToolRisk
from agent_platform.platform.tool_gateway.ports import ToolExecutor

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
        invocation_id: UUID | None = None,
    ) -> object:
        normalized_arguments = _normalize_arguments(arguments)
        try:
            client = await self._client_resolver.resolve(
                tenant_id=definition.tenant_id,
                server_id=definition.server_id,
                credentials=credentials,
            )
            result = await client.call_tool(
                definition.name,
                normalized_arguments,
                invocation_id=invocation_id,
            )
        except (MCPClientError, MCPClientResolutionError):
            # 已经是经过脱敏的稳定错误类型，保留类型供上层做错误转换。
            raise
        except Exception:
            pass
        else:
            return result
        raise MCPToolExecutionError()


class ResilientToolExecutor:
    """把 MCP 执行错误转换为稳定错误码，并对只读工具做有界重试。

    有副作用的工具（write/external/destructive）绝不自动重试：平台内部
    invocation claim 协议无法替代外部系统幂等，重复执行的语义不确定。
    """

    def __init__(
        self,
        inner: ToolExecutor,
        *,
        max_read_retries: int = 2,
        retry_delay_seconds: float = 0.2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_read_retries < 0:
            raise ValueError("max_read_retries must be >= 0")
        self._inner = inner
        self._max_read_retries = max_read_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep

    async def execute(
        self,
        *,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        credentials: Mapping[str, str],
        invocation_id: UUID | None = None,
    ) -> object:
        attempts = (
            self._max_read_retries + 1 if definition.risk is ToolRisk.READ else 1
        )
        failure_code = "tool_execution_failed"
        for attempt in range(attempts):
            try:
                return await self._inner.execute(
                    definition=definition,
                    arguments=arguments,
                    credentials=credentials,
                    invocation_id=invocation_id,
                )
            except MCPTimeoutError:
                failure_code = "tool_timeout"
            except MCPRemoteError:
                failure_code = "tool_remote_error"
            except MCPToolExecutionError:
                raise ToolExecutionFailure("tool_execution_failed") from None
            except MCPClientResolutionError as failure:
                raise ToolExecutionFailure(failure.code) from None
            except ToolExecutionFailure:
                raise
            except Exception:
                raise ToolExecutionFailure("tool_execution_failed") from None
            if attempt + 1 < attempts:
                await self._sleep(self._retry_delay_seconds)
        raise ToolExecutionFailure(failure_code) from None


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
