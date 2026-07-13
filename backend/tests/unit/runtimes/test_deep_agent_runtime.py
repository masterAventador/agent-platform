import asyncio
from typing import Any
from uuid import uuid4

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.messages import AIMessage

from agent_platform.platform.runs.events import EventType
from agent_platform.runtimes.base import EmployeeRuntime, RuntimeStartRequest
from agent_platform.runtimes.deep_agent import (
    DeepAgentRuntime,
    DeepAgentSandboxBackendValidator,
    InvalidDeepAgentBackend,
    RuntimeOperationNotSupported,
)


class FakeAgentGraph:
    async def ainvoke(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
    ) -> dict[str, Any]:
        del input_data, config
        return {
            "messages": [AIMessage(content="这是平台允许输出的答案")],
            "private_graph_state": object(),
        }


class BlockingAgentGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.side_effects = 0

    async def ainvoke(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
    ) -> dict[str, Any]:
        del input_data, config
        self.started.set()
        try:
            await asyncio.Event().wait()
            self.side_effects += 1
            return {"messages": [AIMessage(content="不应完成")]}
        finally:
            self.stopped.set()


def test_sandbox_backend_validator_uses_deep_agents_public_protocol() -> None:
    DeepAgentSandboxBackendValidator.validate(SandboxBackendProtocol())

    with pytest.raises(InvalidDeepAgentBackend):
        DeepAgentSandboxBackendValidator.validate(object())


@pytest.mark.asyncio
async def test_deep_agent_runtime_maps_result_to_platform_contract() -> None:
    runtime = DeepAgentRuntime(agent_factory=lambda request: FakeAgentGraph())
    assert isinstance(runtime, EmployeeRuntime)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="thread-deep-agent",
        employee_definition={"system_prompt": "你是研究助理"},
        input_data={"topic": "Deep Agents"},
    )

    state = await runtime.start(request)
    history = await runtime.get_history(request.run_id)

    assert state.status.value == "completed"
    assert state.data == {"output": "这是平台允许输出的答案"}
    assert [event.type for event in history] == [
        EventType.RUN_STARTED,
        EventType.MESSAGE_OUTPUT,
        EventType.RUN_COMPLETED,
    ]
    assert [event.sequence for event in history] == [1, 2, 3]
    assert "private_graph_state" not in str([event.model_dump() for event in history])
    streamed_events = [event async for event in runtime.stream(request.run_id, after_sequence=1)]
    assert streamed_events == history[1:]


@pytest.mark.asyncio
async def test_deep_agent_runtime_cancel_stops_active_graph_without_completed_event() -> None:
    graph = BlockingAgentGraph()
    runtime = DeepAgentRuntime(agent_factory=lambda request: graph)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-cancel",
        employee_definition={},
        input_data={},
    )
    start_task = asyncio.create_task(runtime.start(request))
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    await runtime.cancel(request.run_id)
    state = await asyncio.wait_for(start_task, timeout=1)
    history = await runtime.get_history(request.run_id)

    assert state.status.value == "cancelled"
    assert graph.side_effects == 0
    assert [event.type for event in history].count(EventType.RUN_CANCELLED) == 1
    assert EventType.RUN_COMPLETED not in [event.type for event in history]


@pytest.mark.asyncio
async def test_deep_agent_runtime_parent_cancellation_awaits_graph_cleanup() -> None:
    graph = BlockingAgentGraph()
    runtime = DeepAgentRuntime(agent_factory=lambda request: graph)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-parent-cancel",
        employee_definition={},
        input_data={},
    )
    start_task = asyncio.create_task(runtime.start(request))
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert graph.stopped.is_set()
    assert runtime._active_tasks == {}


@pytest.mark.asyncio
async def test_deep_agent_runtime_reentry_does_not_create_second_graph_task() -> None:
    graph = BlockingAgentGraph()
    factory_calls = 0

    def factory(request: RuntimeStartRequest) -> BlockingAgentGraph:
        nonlocal factory_calls
        del request
        factory_calls += 1
        return graph

    runtime = DeepAgentRuntime(agent_factory=factory)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-reentry",
        employee_definition={},
        input_data={},
    )
    start_task = asyncio.create_task(runtime.start(request))
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    with pytest.raises(RuntimeOperationNotSupported):
        await runtime.start(request)

    assert factory_calls == 1
    await runtime.cancel(request.run_id)
    await start_task


@pytest.mark.asyncio
async def test_deep_agent_factory_failure_returns_sanitized_failed_state_and_event() -> None:
    secret = "provider-api-key-must-not-leak"

    def failing_factory(request: RuntimeStartRequest) -> FakeAgentGraph:
        del request
        raise RuntimeError(secret)

    runtime = DeepAgentRuntime(agent_factory=failing_factory)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-factory-failure",
        employee_definition={},
        input_data={},
    )

    state = await runtime.start(request)
    history = await runtime.get_history(request.run_id)

    assert state.status.value == "failed"
    assert state.data == {"error_code": "deep_agent_execution_failed"}
    assert [event.type for event in history] == [
        EventType.RUN_STARTED,
        EventType.RUN_FAILED,
    ]
    assert secret not in repr(state)
    assert secret not in repr(history)
