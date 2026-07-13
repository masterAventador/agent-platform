import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType
from agent_platform.runtimes.base import EmployeeRuntime, RuntimeStartRequest
from agent_platform.runtimes.langgraph import LangGraphAgentGraph, LangGraphRuntime
from agent_platform.runtimes.recovery import (
    RuntimeControlMismatch,
    RuntimeRecoveryUnavailable,
)


class ResearchWorkflowState(TypedDict, total=False):
    input: dict[str, object]
    outline: list[str]
    output: dict[str, object]
    private_notes: str


class BlockingLangGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.side_effects = 0

    async def astream(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
        *,
        stream_mode: str,
    ) -> AsyncIterator[Mapping[str, object]]:
        del input_data, config, stream_mode
        self.started.set()
        try:
            await asyncio.Event().wait()
            self.side_effects += 1
            yield {"finished": {"output": "不应完成"}}
        finally:
            self.stopped.set()

    async def aget_state(self, config: dict[str, object]) -> Any:
        del config
        raise AssertionError("cancelled execution must not inspect a completed snapshot")


def prepare_outline(state: ResearchWorkflowState) -> ResearchWorkflowState:
    del state
    return {
        "outline": ["背景", "结论"],
        "private_notes": "不得暴露给平台事件",
    }


def write_report(state: ResearchWorkflowState) -> ResearchWorkflowState:
    return {"output": {"sections": state["outline"], "status": "ready"}}


@pytest.mark.asyncio
async def test_langgraph_runtime_executes_checkpointed_fixed_workflow() -> None:
    builder = StateGraph(ResearchWorkflowState)
    builder.add_node("prepare_outline", prepare_outline)
    builder.add_node("write_report", write_report)
    builder.add_edge(START, "prepare_outline")
    builder.add_edge("prepare_outline", "write_report")
    builder.add_edge("write_report", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    runtime = LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph))
    assert isinstance(runtime, EmployeeRuntime)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="workflow-thread",
        employee_definition={"name": "固定研究流程"},
        input_data={"topic": "LangGraph"},
    )

    state = await runtime.start(request)
    history = await runtime.get_history(request.run_id)

    assert state.status.value == "completed"
    assert state.data == {"output": {"sections": ["背景", "结论"], "status": "ready"}}
    assert [event.type for event in history] == [
        EventType.RUN_STARTED,
        EventType.RUN_PROGRESS,
        EventType.RUN_PROGRESS,
        EventType.MESSAGE_OUTPUT,
        EventType.RUN_COMPLETED,
    ]
    assert [event.payload.get("step") for event in history[1:3]] == [
        "prepare_outline",
        "write_report",
    ]
    assert "private_notes" not in str([event.model_dump() for event in history])

    config = {"configurable": {"thread_id": request.thread_id}}
    checkpoints = [snapshot async for snapshot in graph.aget_state_history(config)]
    assert len(checkpoints) >= 3


@pytest.mark.asyncio
async def test_langgraph_runtime_cancel_stops_active_graph_without_completed_event() -> None:
    graph = BlockingLangGraph()
    runtime = LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph))
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="langgraph-cancel",
        employee_definition={},
        input_data={},
    )
    start_task = asyncio.create_task(runtime.start(request))
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    await runtime.cancel(request.run_id)
    state = await asyncio.wait_for(start_task, timeout=1)
    history = await runtime.get_history(request.run_id)

    assert state.status is RunStatus.CANCELLED
    assert graph.side_effects == 0
    assert [event.type for event in history].count(EventType.RUN_CANCELLED) == 1
    assert EventType.RUN_COMPLETED not in [event.type for event in history]


@pytest.mark.asyncio
async def test_langgraph_runtime_parent_cancellation_awaits_graph_cleanup() -> None:
    graph = BlockingLangGraph()
    runtime = LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph))
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="langgraph-parent-cancel",
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
async def test_langgraph_factory_failure_returns_sanitized_failed_state_and_event() -> None:
    secret = "checkpoint-password-must-not-leak"

    def failing_factory(request: RuntimeStartRequest) -> LangGraphAgentGraph:
        del request
        raise RuntimeError(secret)

    runtime = LangGraphRuntime(graph_factory=failing_factory)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="langgraph-factory-failure",
        employee_definition={},
        input_data={},
    )

    state = await runtime.start(request)
    history = await runtime.get_history(request.run_id)

    assert state.status.value == "failed"
    assert state.data == {"error_code": "langgraph_execution_failed"}
    assert [event.type for event in history] == [
        EventType.RUN_STARTED,
        EventType.RUN_FAILED,
    ]
    assert secret not in repr(state)
    assert secret not in repr(history)


class ApprovalWorkflowState(TypedDict, total=False):
    input: dict[str, object]
    output: dict[str, object]


class RepeatedApprovalWorkflowState(ApprovalWorkflowState, total=False):
    approval_count: int


APPROVAL_ID = uuid4()


def require_approval(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
    del state
    decision = interrupt({"kind": "approval", "approval_id": str(APPROVAL_ID)})
    return {"output": cast(dict[str, object], decision)}


@pytest.mark.asyncio
async def test_repeated_business_approval_id_is_bound_to_each_interrupt_occurrence() -> None:
    business_approval_id = uuid4()

    def require_repeated_approval(
        state: RepeatedApprovalWorkflowState,
    ) -> RepeatedApprovalWorkflowState:
        decision = interrupt({"kind": "approval", "approval_id": str(business_approval_id)})
        approval_count = state.get("approval_count", 0) + 1
        return {
            "approval_count": approval_count,
            "output": {"approval_count": approval_count, "decision": decision},
        }

    def route_after_approval(state: RepeatedApprovalWorkflowState) -> str:
        return "repeat" if state["approval_count"] < 2 else "complete"

    checkpointer = InMemorySaver()
    builder = StateGraph(RepeatedApprovalWorkflowState)
    builder.add_node("require_approval", require_repeated_approval)
    builder.add_edge(START, "require_approval")
    builder.add_conditional_edges(
        "require_approval",
        route_after_approval,
        {"repeat": "require_approval", "complete": END},
    )
    graph = builder.compile(checkpointer=checkpointer)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="repeated-business-approval-id",
        employee_definition={},
        input_data={},
    )
    runtime = LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph))

    first_waiting = await runtime.start(request)
    first_history = await runtime.get_history(request.run_id)
    first_platform_id = UUID(str(first_history[-1].payload["approval_id"]))

    assert first_waiting.status is RunStatus.WAITING_FOR_APPROVAL
    assert first_platform_id != business_approval_id

    await runtime.approve(request.run_id, first_platform_id)
    second_history = await runtime.get_history(request.run_id)
    second_platform_id = UUID(str(second_history[-1].payload["approval_id"]))

    assert (await runtime.get_state(request.run_id)).status is RunStatus.WAITING_FOR_APPROVAL
    assert second_platform_id not in {business_approval_id, first_platform_id}

    restored_runtime = LangGraphRuntime(
        graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
    )
    restored = await restored_runtime.recover(request, RunStatus.WAITING_FOR_APPROVAL)
    restored_history = await restored_runtime.get_history(request.run_id)

    assert restored.status is RunStatus.WAITING_FOR_APPROVAL
    assert restored_history[-1].payload["approval_id"] == str(second_platform_id)
    with pytest.raises(RuntimeControlMismatch):
        await restored_runtime.approve(request.run_id, first_platform_id)
    restored_after_replay = await restored_runtime.get_state(request.run_id)
    assert restored_after_replay.status is RunStatus.WAITING_FOR_APPROVAL

    await restored_runtime.approve(request.run_id, second_platform_id)
    assert (await restored_runtime.get_state(request.run_id)).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_langgraph_runtime_recovers_interrupt_and_approves_from_checkpoint() -> None:
    checkpointer = InMemorySaver()
    builder = StateGraph(ApprovalWorkflowState)
    builder.add_node("require_approval", require_approval)
    builder.add_edge(START, "require_approval")
    builder.add_edge("require_approval", END)
    graph = builder.compile(checkpointer=checkpointer)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="approval-recovery-thread",
        employee_definition={},
        input_data={},
    )
    first_runtime = LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph))

    waiting = await first_runtime.start(request)
    assert waiting.status.value == "waiting_for_approval"
    waiting_history = await first_runtime.get_history(request.run_id)
    platform_approval_id = UUID(str(waiting_history[-1].payload["approval_id"]))
    assert waiting_history[-1].payload == {
        "status": "waiting_for_approval",
        "approval_id": str(platform_approval_id),
    }
    assert platform_approval_id != APPROVAL_ID
    with pytest.raises(RuntimeControlMismatch):
        await first_runtime.resume(request.run_id)

    restored_runtime = LangGraphRuntime(
        graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
    )
    restored = await restored_runtime.recover(request, waiting.status)
    restored_history = await restored_runtime.get_history(request.run_id)
    assert restored_history[-1].payload["approval_id"] == str(platform_approval_id)
    with pytest.raises(RuntimeControlMismatch):
        await restored_runtime.approve(request.run_id, uuid4())
    assert (await restored_runtime.get_state(request.run_id)).status is waiting.status

    await restored_runtime.approve(request.run_id, platform_approval_id)
    completed = await restored_runtime.get_state(request.run_id)

    assert restored.status is waiting.status
    assert completed.status.value == "completed"
    assert completed.data["output"] == {
        "action": "approve",
        "approval_id": str(platform_approval_id),
    }

    reconciled_runtime = LangGraphRuntime(
        graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
    )
    reconciled = await reconciled_runtime.recover(
        request,
        RunStatus.WAITING_FOR_APPROVAL,
    )
    assert reconciled.status is RunStatus.COMPLETED
    reconciled_history = await reconciled_runtime.get_history(request.run_id)
    assert [event.type for event in reconciled_history] == [
        EventType.MESSAGE_OUTPUT,
        EventType.RUN_COMPLETED,
    ]
    assert reconciled_history[0].payload == {
        "content": {
            "action": "approve",
            "approval_id": str(platform_approval_id),
        }
    }


@pytest.mark.asyncio
async def test_langgraph_runtime_refuses_recovery_without_checkpoint() -> None:
    builder = StateGraph(ApprovalWorkflowState)
    builder.add_node("require_approval", require_approval)
    builder.add_edge(START, "require_approval")
    builder.add_edge("require_approval", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="missing-checkpoint",
        employee_definition={},
        input_data={},
    )

    with pytest.raises(RuntimeRecoveryUnavailable):
        await LangGraphRuntime(
            graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
        ).recover(request, RunStatus.WAITING_FOR_APPROVAL)


@pytest.mark.asyncio
async def test_langgraph_reject_resumes_matching_interrupt_to_cancelled_terminal() -> None:
    graph_builder = StateGraph(ApprovalWorkflowState)
    graph_builder.add_node("require_approval", require_approval)
    graph_builder.add_edge(START, "require_approval")
    graph_builder.add_edge("require_approval", END)
    graph = graph_builder.compile(checkpointer=InMemorySaver())
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="approval-reject-thread",
        employee_definition={},
        input_data={},
    )
    runtime = LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph))
    await runtime.start(request)
    history = await runtime.get_history(request.run_id)
    platform_approval_id = UUID(str(history[-1].payload["approval_id"]))

    await runtime.reject(request.run_id, platform_approval_id, "operator rejected")

    assert (await runtime.get_state(request.run_id)).status is RunStatus.CANCELLED
    assert (await runtime.get_history(request.run_id))[-1].type is EventType.RUN_CANCELLED


@pytest.mark.asyncio
async def test_langgraph_business_reject_output_still_recovers_completed() -> None:
    business_approval_id = uuid4()

    def business_rejection(
        state: ApprovalWorkflowState,
    ) -> ApprovalWorkflowState:
        del state
        return {
            "output": {
                "action": "reject",
                "approval_id": str(business_approval_id),
                "reason": "business rule declined the record",
            }
        }

    graph_builder = StateGraph(ApprovalWorkflowState)
    graph_builder.add_node("business_rejection", business_rejection)
    graph_builder.add_edge(START, "business_rejection")
    graph_builder.add_edge("business_rejection", END)
    graph = graph_builder.compile(checkpointer=InMemorySaver())
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="business-reject-output-recovery",
        employee_definition={},
        input_data={},
    )
    await LangGraphRuntime(graph_factory=lambda request: cast(LangGraphAgentGraph, graph)).start(
        request
    )

    recovered = await LangGraphRuntime(
        graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
    ).recover(request, RunStatus.WAITING_FOR_APPROVAL)

    assert recovered.status is RunStatus.COMPLETED
    assert recovered.data == {
        "output": {
            "action": "reject",
            "approval_id": str(business_approval_id),
            "reason": "business rule declined the record",
        }
    }


@pytest.mark.asyncio
async def test_langgraph_runtime_fails_closed_on_multiple_simultaneous_interrupts() -> None:
    first_id = uuid4()
    second_id = uuid4()

    def first_interrupt(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        del state
        interrupt({"kind": "approval", "approval_id": str(first_id)})
        return {}

    def second_interrupt(state: ApprovalWorkflowState) -> ApprovalWorkflowState:
        del state
        interrupt({"kind": "approval", "approval_id": str(second_id)})
        return {}

    builder = StateGraph(ApprovalWorkflowState)
    builder.add_node("first", first_interrupt)
    builder.add_node("second", second_interrupt)
    builder.add_edge(START, "first")
    builder.add_edge(START, "second")
    builder.add_edge("first", END)
    builder.add_edge("second", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="multiple-interrupts",
        employee_definition={},
        input_data={},
    )

    state = await LangGraphRuntime(
        graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
    ).start(request)

    assert state.status is RunStatus.FAILED
