from typing import TypedDict, cast
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent_platform.platform.runs.events import EventType
from agent_platform.runtimes.base import EmployeeRuntime, RuntimeStartRequest
from agent_platform.runtimes.langgraph import LangGraphAgentGraph, LangGraphRuntime


class ResearchWorkflowState(TypedDict, total=False):
    input: dict[str, object]
    outline: list[str]
    output: dict[str, object]
    private_notes: str


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
    runtime = LangGraphRuntime(
        graph_factory=lambda request: cast(LangGraphAgentGraph, graph)
    )
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
