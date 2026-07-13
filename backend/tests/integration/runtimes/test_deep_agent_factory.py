from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import JsonValue

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType
from agent_platform.platform.tool_gateway import (
    PolicyContext,
    PolicyDecision,
    ToolInvocation,
    ToolInvocationOutcome,
)
from agent_platform.platform.tools.entities import ToolRiskLevel
from agent_platform.runtimes.base import RuntimeStartRequest
from agent_platform.runtimes.deep_agent import DeepAgentFactory, DeepAgentRuntime
from agent_platform.runtimes.recovery import RuntimeControlMismatch
from agent_platform.runtimes.tool_gateway_adapter import (
    InvocationContext,
    OneTimeToolApprovalStore,
    ToolApprovalRequired,
    ToolGatewayAdapter,
)


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


class CapturingAgentGraph:
    async def ainvoke(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
    ) -> Mapping[str, object]:
        del input_data, config
        return {"messages": []}


def test_official_deep_agent_factory_passes_skill_paths_to_public_parameter() -> None:
    captured: dict[str, object] = {}

    def agent_builder(**kwargs: object) -> object:
        captured.update(kwargs)
        return CapturingAgentGraph()

    skill_paths: list[JsonValue] = [
        "/workspace/skills/report-writer",
        "/workspace/skills/research",
    ]
    factory = DeepAgentFactory(
        model="test-model",
        tools=[],
        agent_builder=agent_builder,
    )
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="skill-enabled-deep-agent",
        employee_definition={
            "system_prompt": "按 Skill 执行任务",
            "skill_paths": skill_paths,
        },
        input_data={"message": "开始"},
    )

    graph = factory(request)

    assert isinstance(graph, CapturingAgentGraph)
    assert captured["skills"] == skill_paths


@pytest.mark.asyncio
async def test_official_deep_agent_factory_runs_with_injected_model() -> None:
    model = ToolBindingFakeChatModel(messages=iter(["官方 Deep Agents 调用成功"]))
    runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=model,
            tools=[],
            backend=SandboxBackendProtocol(),
        )
    )
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="official-deep-agent",
        employee_definition={"system_prompt": "返回测试结果"},
        input_data={"message": "开始"},
    )

    state = await runtime.start(request)

    assert state.status.value == "completed", await runtime.get_history(request.run_id)
    assert state.data == {"output": "官方 Deep Agents 调用成功"}


@tool
def approval_required_tool(value: str) -> str:
    """A test tool requiring human approval."""

    return value


@dataclass(frozen=True)
class ExternalToolMetadata:
    id: UUID
    name: str = "external_operation"
    description: str = "Perform an external operation."
    input_schema: dict[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
    )
    risk_level: ToolRiskLevel = ToolRiskLevel.EXTERNAL


class ApprovalAwareGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[ToolInvocation, PolicyContext]] = []

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome:
        self.calls.append((invocation, context))
        if not context.approval_granted:
            return ToolInvocationOutcome(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason="approval_required",
            )
        return ToolInvocationOutcome(
            decision=PolicyDecision.ALLOW,
            result="external result",
        )


@pytest.mark.asyncio
async def test_deep_agent_runtime_recovers_real_human_interrupt_and_checks_approval_id() -> None:
    checkpointer = InMemorySaver()
    first_model = ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "approval_required_tool",
                            "args": {"value": "approved value"},
                            "id": "approval-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-runtime-recovery",
        employee_definition={},
        input_data={"task": "dangerous"},
    )
    first_runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=first_model,
            tools=[approval_required_tool],
            backend=SandboxBackendProtocol(),
            checkpointer=checkpointer,
            interrupt_on={"approval_required_tool": True},
        )
    )

    waiting = await first_runtime.start(request)
    waiting_history = await first_runtime.get_history(request.run_id)
    approval_events = [
        event for event in waiting_history if event.type.value == "approval.required"
    ]

    assert waiting.status.value == "waiting_for_approval"
    assert len(approval_events) == 1
    approval_id = UUID(str(approval_events[0].payload["approval_id"]))

    restored_runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=ToolBindingFakeChatModel(messages=iter([AIMessage(content="approved")])),
            tools=[approval_required_tool],
            backend=SandboxBackendProtocol(),
            checkpointer=checkpointer,
            interrupt_on={"approval_required_tool": True},
        ),
        approval_store=(approval_store := OneTimeToolApprovalStore()),
        tool_ids_by_name={"approval_required_tool": (tool_id := uuid4())},
    )
    await restored_runtime.recover(request, waiting.status)
    with pytest.raises(RuntimeControlMismatch):
        await restored_runtime.approve(request.run_id, uuid4())
    await restored_runtime.approve(request.run_id, approval_id)

    completed = await restored_runtime.get_state(request.run_id)
    assert completed.status.value == "completed"
    assert completed.data == {"output": "approved"}
    assert not approval_store.consume(
        run_id=request.run_id,
        tool_id=tool_id,
        tool_name="approval_required_tool",
        arguments={"value": "approved value"},
    )

    reconciled_runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=ToolBindingFakeChatModel(messages=iter([])),
            tools=[approval_required_tool],
            backend=SandboxBackendProtocol(),
            checkpointer=checkpointer,
            interrupt_on={"approval_required_tool": True},
        )
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
    assert reconciled_history[0].payload == {"content": "approved"}


@pytest.mark.asyncio
async def test_deep_agent_runtime_rotates_approval_id_for_consecutive_interrupts() -> None:
    checkpointer = InMemorySaver()
    model = ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "approval_required_tool",
                            "args": {"value": "first"},
                            "id": "first-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "approval_required_tool",
                            "args": {"value": "second"},
                            "id": "second-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="all approved"),
            ]
        )
    )
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-consecutive-approvals",
        employee_definition={},
        input_data={},
    )
    runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=model,
            tools=[approval_required_tool],
            backend=SandboxBackendProtocol(),
            checkpointer=checkpointer,
            interrupt_on={"approval_required_tool": True},
        )
    )

    await runtime.start(request)
    first_id = UUID(str((await runtime.get_history(request.run_id))[-1].payload["approval_id"]))
    await runtime.approve(request.run_id, first_id)
    second_id = UUID(str((await runtime.get_history(request.run_id))[-1].payload["approval_id"]))

    assert second_id != first_id
    assert (await runtime.get_state(request.run_id)).status.value == "waiting_for_approval"
    with pytest.raises(RuntimeControlMismatch):
        await runtime.approve(request.run_id, first_id)
    await runtime.approve(request.run_id, second_id)
    assert (await runtime.get_state(request.run_id)).data == {"output": "all approved"}


@pytest.mark.asyncio
async def test_deep_agent_runtime_rejects_matching_interrupt_without_executing_tool() -> None:
    checkpointer = InMemorySaver()
    model = ToolBindingFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "approval_required_tool",
                            "args": {"value": "must-not-run"},
                            "id": "rejected-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="rejection observed"),
            ]
        )
    )
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="deep-agent-reject",
        employee_definition={},
        input_data={},
    )
    runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=model,
            tools=[approval_required_tool],
            backend=SandboxBackendProtocol(),
            checkpointer=checkpointer,
            interrupt_on={"approval_required_tool": True},
        )
    )

    await runtime.start(request)
    approval_id = UUID(str((await runtime.get_history(request.run_id))[-1].payload["approval_id"]))
    await runtime.reject(request.run_id, approval_id, "operator rejected")

    assert (await runtime.get_state(request.run_id)).status.value == "cancelled"


@pytest.mark.asyncio
async def test_real_deep_agent_approval_is_consumed_once_by_exact_gateway_invocation() -> None:
    run_id = uuid4()
    metadata = ExternalToolMetadata(id=uuid4())
    gateway = ApprovalAwareGateway()
    approvals = OneTimeToolApprovalStore()
    adapted_tool = ToolGatewayAdapter(
        gateway=gateway,
        invocation_context=InvocationContext(
            tenant_id=uuid4(),
            run_id=run_id,
            employee_id=uuid4(),
            user_id=uuid4(),
        ),
        policy_context=PolicyContext(allowed_tool_ids=frozenset({metadata.id})),
        approval_store=approvals,
    ).adapt(metadata)
    runtime = DeepAgentRuntime(
        agent_factory=DeepAgentFactory(
            model=ToolBindingFakeChatModel(
                messages=iter(
                    [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": metadata.name,
                                    "args": {"value": "approved value"},
                                    "id": "external-call",
                                    "type": "tool_call",
                                }
                            ],
                        ),
                        AIMessage(content="external completed"),
                    ]
                )
            ),
            tools=[adapted_tool],
            backend=SandboxBackendProtocol(),
            checkpointer=InMemorySaver(),
            interrupt_on={metadata.name: True},
        ),
        approval_store=approvals,
        tool_ids_by_name={metadata.name: metadata.id},
    )
    request = RuntimeStartRequest(
        run_id=run_id,
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="real-gateway-approval",
        employee_definition={},
        input_data={},
    )

    await runtime.start(request)
    approval_id = UUID(str((await runtime.get_history(run_id))[-1].payload["approval_id"]))
    await runtime.approve(run_id, approval_id)

    assert (await runtime.get_state(run_id)).status.value == "completed"
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1].approval_granted is True
    with pytest.raises(ToolApprovalRequired):
        await adapted_tool.ainvoke({"value": "approved value"})
    assert gateway.calls[-1][1].approval_granted is False
