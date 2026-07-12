import os
from collections.abc import Callable, Sequence
from typing import Any, TypedDict, cast
from uuid import UUID, uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent_platform.infrastructure.checkpoints.postgres import postgres_checkpointer
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.runtimes.base import RuntimeStartRequest
from agent_platform.runtimes.deep_agent import DeepAgentFactory, DeepAgentRuntime
from agent_platform.runtimes.langgraph import LangGraphAgentGraph, LangGraphRuntime


class CounterState(TypedDict):
    count: int


def increment(state: CounterState) -> CounterState:
    return {"count": state["count"] + 1}


def test_checkpoint_serializer_rejects_arbitrary_python_objects_without_pickle() -> None:
    class UntrustedObject:
        pass

    serializer = JsonPlusSerializer(allowed_msgpack_modules=())

    with pytest.raises(TypeError, match="not msgpack serializable"):
        serializer.dumps_typed(UntrustedObject())


class ToolBindingFakeChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del tools, tool_choice, kwargs
        return self


@tool
def dangerous_operation(value: str) -> str:
    """A test operation that always requires human approval."""

    return value


@pytest.mark.asyncio
async def test_postgres_checkpointer_restores_thread_across_instances() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL Checkpointer 测试")
    checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    thread_id = f"checkpoint-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    builder = StateGraph(CounterState)
    builder.add_node("increment", increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)

    async with postgres_checkpointer(checkpoint_url) as checkpointer:
        await checkpointer.setup()
        graph = builder.compile(checkpointer=checkpointer)
        assert (await graph.ainvoke({"count": 0}, config))["count"] == 1

    async with postgres_checkpointer(checkpoint_url) as restored_checkpointer:
        restored_graph = builder.compile(checkpointer=restored_checkpointer)
        snapshot = await restored_graph.aget_state(config)
        assert snapshot.values["count"] == 1


@pytest.mark.asyncio
async def test_postgres_checkpointer_restores_real_deep_agent_messages_and_interrupt() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL Deep Agent 恢复测试")
    checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    thread_id = f"deep-agent-checkpoint-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    model = ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "dangerous_operation",
                            "args": {"value": "checkpoint-value"},
                            "id": "dangerous-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )

    async with postgres_checkpointer(checkpoint_url) as checkpointer:
        await checkpointer.setup()
        first_graph = create_deep_agent(
            model=model,
            tools=[dangerous_operation],
            backend=SandboxBackendProtocol(),
            interrupt_on={"dangerous_operation": True},
            checkpointer=checkpointer,
        )
        await first_graph.ainvoke(
            {"messages": [HumanMessage(content="run the operation")]},
            config,
        )

    restored_model = ToolBindingFakeChatModel(messages=iter([AIMessage(content="unused")]))
    async with postgres_checkpointer(checkpoint_url) as restored_checkpointer:
        restored_graph = create_deep_agent(
            model=restored_model,
            tools=[dangerous_operation],
            backend=SandboxBackendProtocol(),
            interrupt_on={"dangerous_operation": True},
            checkpointer=restored_checkpointer,
        )
        snapshot = await restored_graph.aget_state(config)

    assert [type(message) for message in snapshot.values["messages"]] == [
        HumanMessage,
        AIMessage,
    ]
    assert snapshot.next == ("HumanInTheLoopMiddleware.after_model",)
    interrupt_value = snapshot.tasks[0].interrupts[0].value
    assert interrupt_value["action_requests"][0]["name"] == "dangerous_operation"
    assert interrupt_value["action_requests"][0]["args"] == {
        "value": "checkpoint-value"
    }


class ApprovalState(TypedDict, total=False):
    input: dict[str, object]
    output: dict[str, object]


@pytest.mark.asyncio
async def test_postgres_runtime_closes_rebuilds_approves_and_reads_final_checkpoint() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL runtime 恢复测试")
    checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    approval_id = uuid4()

    def approval_node(state: ApprovalState) -> ApprovalState:
        del state
        decision = interrupt(
            {"kind": "approval", "approval_id": str(approval_id)}
        )
        return {"output": cast(dict[str, object], decision)}

    builder = StateGraph(ApprovalState)
    builder.add_node("approval", approval_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id=f"runtime-approval-{uuid4()}",
        employee_definition={},
        input_data={},
    )

    async with postgres_checkpointer(checkpoint_url) as first_checkpointer:
        first_graph = builder.compile(checkpointer=first_checkpointer)
        waiting = await LangGraphRuntime(
            graph_factory=lambda request: cast(LangGraphAgentGraph, first_graph)
        ).start(request)
        assert waiting.status is RunStatus.WAITING_FOR_APPROVAL

    async with postgres_checkpointer(checkpoint_url) as second_checkpointer:
        second_graph = builder.compile(checkpointer=second_checkpointer)
        restored_runtime = LangGraphRuntime(
            graph_factory=lambda request: cast(LangGraphAgentGraph, second_graph)
        )
        await restored_runtime.recover(request, RunStatus.WAITING_FOR_APPROVAL)
        await restored_runtime.approve(request.run_id, approval_id)
        assert (await restored_runtime.get_state(request.run_id)).status is RunStatus.COMPLETED

    async with postgres_checkpointer(checkpoint_url) as final_checkpointer:
        final_graph = builder.compile(checkpointer=final_checkpointer)
        snapshot = await final_graph.aget_state(
            {"configurable": {"thread_id": request.thread_id}}
        )

    assert snapshot.next == ()
    assert snapshot.values["output"] == {
        "action": "approve",
        "approval_id": str(approval_id),
    }

    rejected_request = request.model_copy(
        update={
            "run_id": uuid4(),
            "thread_id": f"runtime-reject-{uuid4()}",
        }
    )
    async with postgres_checkpointer(checkpoint_url) as reject_start_checkpointer:
        reject_start_graph = builder.compile(checkpointer=reject_start_checkpointer)
        reject_start_runtime = LangGraphRuntime(
            graph_factory=lambda request: cast(
                LangGraphAgentGraph,
                reject_start_graph,
            )
        )
        assert (
            await reject_start_runtime.start(rejected_request)
        ).status is RunStatus.WAITING_FOR_APPROVAL

    async with postgres_checkpointer(checkpoint_url) as reject_checkpointer:
        reject_graph = builder.compile(checkpointer=reject_checkpointer)
        reject_runtime = LangGraphRuntime(
            graph_factory=lambda request: cast(LangGraphAgentGraph, reject_graph)
        )
        await reject_runtime.recover(
            rejected_request,
            RunStatus.WAITING_FOR_APPROVAL,
        )
        await reject_runtime.reject(
            rejected_request.run_id,
            approval_id,
            "operator rejected",
        )
        assert (
            await reject_runtime.get_state(rejected_request.run_id)
        ).status is RunStatus.CANCELLED

    async with postgres_checkpointer(checkpoint_url) as reject_final_checkpointer:
        reject_final_graph = builder.compile(checkpointer=reject_final_checkpointer)
        rejected_snapshot = await reject_final_graph.aget_state(
            {"configurable": {"thread_id": rejected_request.thread_id}}
        )
        recovered_after_reject = LangGraphRuntime(
            graph_factory=lambda request: cast(
                LangGraphAgentGraph,
                reject_final_graph,
            )
        )
        recovered_reject_state = await recovered_after_reject.recover(
            rejected_request,
            RunStatus.WAITING_FOR_APPROVAL,
        )
        recovered_reject_history = await recovered_after_reject.get_history(
            rejected_request.run_id
        )

    assert rejected_snapshot.next == ()
    assert rejected_snapshot.values["output"] == {
        "action": "reject",
        "approval_id": str(approval_id),
        "reason": "operator rejected",
    }
    assert rejected_snapshot.metadata["agent_platform_terminal_status"] == "cancelled"
    assert recovered_reject_state.status is RunStatus.CANCELLED
    assert recovered_reject_history[-1].type.value == "run.cancelled"


@pytest.mark.asyncio
async def test_postgres_deep_agent_reject_checkpoint_recovers_cancelled() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL Deep Agent 恢复测试")
    checkpoint_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id=f"deep-agent-reject-recovery-{uuid4()}",
        employee_definition={},
        input_data={"task": "dangerous"},
    )
    first_model = ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "dangerous_operation",
                            "args": {"value": "must-not-run"},
                            "id": "reject-recovery-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )

    async with postgres_checkpointer(checkpoint_url) as start_checkpointer:
        await start_checkpointer.setup()
        start_runtime = DeepAgentRuntime(
            agent_factory=DeepAgentFactory(
                model=first_model,
                tools=[dangerous_operation],
                backend=SandboxBackendProtocol(),
                checkpointer=start_checkpointer,
                interrupt_on={"dangerous_operation": True},
            )
        )
        waiting = await start_runtime.start(request)
        approval_id = UUID(
            str((await start_runtime.get_history(request.run_id))[-1].payload["approval_id"])
        )
        assert waiting.status is RunStatus.WAITING_FOR_APPROVAL

    async with postgres_checkpointer(checkpoint_url) as reject_checkpointer:
        reject_runtime = DeepAgentRuntime(
            agent_factory=DeepAgentFactory(
                model=ToolBindingFakeChatModel(
                    messages=iter([AIMessage(content="rejection observed")])
                ),
                tools=[dangerous_operation],
                backend=SandboxBackendProtocol(),
                checkpointer=reject_checkpointer,
                interrupt_on={"dangerous_operation": True},
            )
        )
        await reject_runtime.recover(request, waiting.status)
        await reject_runtime.reject(request.run_id, approval_id, "operator rejected")
        assert (await reject_runtime.get_state(request.run_id)).status is RunStatus.CANCELLED

    async with postgres_checkpointer(checkpoint_url) as recovery_checkpointer:
        recovered_runtime = DeepAgentRuntime(
            agent_factory=DeepAgentFactory(
                model=ToolBindingFakeChatModel(messages=iter([])),
                tools=[dangerous_operation],
                backend=SandboxBackendProtocol(),
                checkpointer=recovery_checkpointer,
                interrupt_on={"dangerous_operation": True},
            )
        )
        recovered = await recovered_runtime.recover(
            request,
            RunStatus.WAITING_FOR_APPROVAL,
        )

    assert recovered.status is RunStatus.CANCELLED
    assert (await recovered_runtime.get_history(request.run_id))[-1].type.value == (
        "run.cancelled"
    )
