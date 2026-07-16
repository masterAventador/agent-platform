"""C09 ResilientToolExecutor：超时、重试与稳定错误码转换。"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from agent_platform.infrastructure.mcp.errors import (
    MCPRemoteError,
    MCPTimeoutError,
    MCPToolExecutionError,
)
from agent_platform.infrastructure.mcp.executor import ResilientToolExecutor
from agent_platform.infrastructure.mcp.resolver import MCPServerUnavailableError
from agent_platform.platform.tool_gateway import ToolDefinition, ToolExecutionFailure, ToolRisk

TENANT_ID = uuid4()


@dataclass
class ScriptedExecutor:
    failures: list[Exception] = field(default_factory=list)
    calls: int = 0
    result: object = "ok"

    async def execute(self, *, definition, arguments, credentials, invocation_id) -> object:
        del definition, arguments, credentials, invocation_id
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


def definition(risk: ToolRisk) -> ToolDefinition:
    return ToolDefinition(
        tenant_id=TENANT_ID,
        tool_id=uuid4(),
        server_id=uuid4(),
        name="tool",
        risk=risk,
    )


async def _no_sleep(seconds: float) -> None:
    del seconds


async def _run(executor: ResilientToolExecutor, risk: ToolRisk) -> object:
    return await executor.execute(
        definition=definition(risk),
        arguments={},
        credentials={},
        invocation_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_read_tool_retries_transient_failures_then_succeeds() -> None:
    inner = ScriptedExecutor(failures=[MCPTimeoutError(), MCPRemoteError()])
    executor = ResilientToolExecutor(inner, max_read_retries=2, sleep=_no_sleep)

    assert await _run(executor, ToolRisk.READ) == "ok"
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_read_tool_retry_budget_is_bounded() -> None:
    inner = ScriptedExecutor(failures=[MCPTimeoutError()] * 5)
    executor = ResilientToolExecutor(inner, max_read_retries=2, sleep=_no_sleep)

    with pytest.raises(ToolExecutionFailure) as failure:
        await _run(executor, ToolRisk.READ)
    assert failure.value.code == "tool_timeout"
    assert inner.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", [ToolRisk.WRITE, ToolRisk.EXTERNAL, ToolRisk.DESTRUCTIVE])
async def test_side_effect_tools_never_retry(risk: ToolRisk) -> None:
    inner = ScriptedExecutor(failures=[MCPTimeoutError()])
    executor = ResilientToolExecutor(inner, max_read_retries=2, sleep=_no_sleep)

    with pytest.raises(ToolExecutionFailure) as failure:
        await _run(executor, risk)
    assert failure.value.code == "tool_timeout"
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_tool_reported_error_is_not_retried_even_for_read() -> None:
    inner = ScriptedExecutor(failures=[MCPToolExecutionError()])
    executor = ResilientToolExecutor(inner, max_read_retries=2, sleep=_no_sleep)

    with pytest.raises(ToolExecutionFailure) as failure:
        await _run(executor, ToolRisk.READ)
    assert failure.value.code == "tool_execution_failed"
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_resolution_errors_map_to_their_stable_codes() -> None:
    inner = ScriptedExecutor(failures=[MCPServerUnavailableError()])
    executor = ResilientToolExecutor(inner, max_read_retries=0, sleep=_no_sleep)

    with pytest.raises(ToolExecutionFailure) as failure:
        await _run(executor, ToolRisk.READ)
    assert failure.value.code == "mcp_server_unavailable"


@pytest.mark.asyncio
async def test_unknown_errors_are_sanitized() -> None:
    inner = ScriptedExecutor(failures=[RuntimeError("leaks token=abc")])
    executor = ResilientToolExecutor(inner, max_read_retries=0, sleep=_no_sleep)

    with pytest.raises(ToolExecutionFailure) as failure:
        await _run(executor, ToolRisk.READ)
    assert failure.value.code == "tool_execution_failed"
    assert "token=abc" not in str(failure.value)
