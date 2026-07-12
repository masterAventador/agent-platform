import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from deepagents import create_deep_agent
from deepagents.backends import BackendProtocol
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.graph import DeepAgentState
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, StateSnapshot
from pydantic import JsonValue, TypeAdapter

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import (
    PLATFORM_TERMINAL_STATUS_KEY,
    ArtifactReference,
    RuntimeStartRequest,
    RuntimeState,
)
from agent_platform.runtimes.tool_gateway_adapter import OneTimeToolApprovalStore
from agent_platform.workers.runtime_recovery import (
    RuntimeControlMismatch,
    RuntimeRecoveryUnavailable,
)


class RuntimeRunNotFound(Exception):
    """运行时中不存在该任务。"""


class RuntimeOperationNotSupported(Exception):
    """当前自主员工没有可处理的对应操作。"""


class InvalidDeepAgentBackend(TypeError):
    """供应商对象不符合 Deep Agents 公开 Sandbox Backend 契约。"""


class DeepAgentSandboxBackendValidator:
    """供 SandboxManager 在环境进入运行时前执行的官方协议校验器。"""

    @staticmethod
    def validate(backend: object) -> None:
        if not isinstance(backend, SandboxBackendProtocol):
            raise InvalidDeepAgentBackend(type(backend).__name__)


def require_sandbox_backend(backend: object) -> SandboxBackendProtocol:
    DeepAgentSandboxBackendValidator.validate(backend)
    return cast(SandboxBackendProtocol, backend)


class AgentGraph(Protocol):
    async def ainvoke(
        self,
        input_data: dict[str, object] | Command[Any],
        config: dict[str, object],
    ) -> Mapping[str, object]: ...

    async def aget_state(self, config: dict[str, object]) -> StateSnapshot: ...


@dataclass(frozen=True, slots=True)
class PendingToolApproval:
    approval_id: UUID
    tool_name: str
    arguments: dict[str, object]


class PlatformDeepAgentState(DeepAgentState):
    agent_platform_terminal_status: NotRequired[Literal["cancelled"]]


class DeepAgentFactory:
    """仅通过 Deep Agents 公开工厂创建自主员工图。"""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]],
        backend: BackendProtocol | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        interrupt_on: dict[str, bool | dict[str, object]] | None = None,
        agent_builder: Callable[..., object] = create_deep_agent,
    ) -> None:
        self._model = model
        self._tools = tools
        self._backend = backend
        self._checkpointer = checkpointer
        self._interrupt_on = interrupt_on
        self._agent_builder = agent_builder

    def __call__(self, request: RuntimeStartRequest) -> AgentGraph:
        system_prompt = str(request.employee_definition.get("system_prompt", ""))
        raw_skill_paths = request.employee_definition.get("skill_paths")
        skill_paths = (
            TypeAdapter(list[str]).validate_python(raw_skill_paths)
            if raw_skill_paths is not None
            else None
        )
        graph = self._agent_builder(
            model=self._model,
            tools=self._tools,
            system_prompt=system_prompt,
            backend=self._backend,
            skills=skill_paths,
            checkpointer=self._checkpointer,
            interrupt_on=self._interrupt_on,
            state_schema=PlatformDeepAgentState,
        )
        return cast(AgentGraph, graph)


class DeepAgentRuntime:
    def __init__(
        self,
        *,
        agent_factory: Callable[[RuntimeStartRequest], AgentGraph],
        approval_store: OneTimeToolApprovalStore | None = None,
        tool_ids_by_name: Mapping[str, UUID] | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._graphs: dict[UUID, AgentGraph] = {}
        self._requests: dict[UUID, RuntimeStartRequest] = {}
        self._states: dict[UUID, RuntimeState] = {}
        self._history: dict[UUID, list[PlatformEvent]] = {}
        self._approval_store = approval_store
        self._tool_ids_by_name = dict(tool_ids_by_name or {})
        self._pending_approvals: dict[UUID, PendingToolApproval] = {}

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self._requests[request.run_id] = request
        self._history[request.run_id] = []
        self._append_event(
            request,
            EventType.RUN_STARTED,
            {"thread_id": request.thread_id},
        )

        try:
            graph = self._agent_factory(request)
            self._graphs[request.run_id] = graph
            result = await self._invoke_result(graph, request, request.input_data)
            approval = None
            if result.get("__interrupt__"):
                snapshot = await graph.aget_state(self._config(request))
                approval = self._approval(snapshot, run_id=request.run_id)
            if approval is not None:
                self._pending_approvals[request.run_id] = approval
                self._append_event(
                    request,
                    EventType.APPROVAL_REQUIRED,
                    {
                        "status": "waiting_for_approval",
                        "approval_id": str(approval.approval_id),
                    },
                )
                state = RuntimeState(
                    run_id=request.run_id,
                    status=RunStatus.WAITING_FOR_APPROVAL,
                    data={},
                )
                self._states[request.run_id] = state
                return state
            output = self._output(result)
        except Exception as error:
            self._append_event(
                request,
                EventType.RUN_FAILED,
                {
                    "code": "deep_agent_execution_failed",
                    "error_type": type(error).__name__,
                },
            )
            state = RuntimeState(
                run_id=request.run_id,
                status=RunStatus.FAILED,
                data={"error_code": "deep_agent_execution_failed"},
            )
            self._states[request.run_id] = state
            return state

        self._append_event(request, EventType.MESSAGE_OUTPUT, {"content": output})
        self._append_event(request, EventType.RUN_COMPLETED, {"status": "completed"})
        state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.COMPLETED,
            data={"output": output},
        )
        self._states[request.run_id] = state
        return state

    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        if status is not RunStatus.WAITING_FOR_APPROVAL:
            raise RuntimeRecoveryUnavailable
        graph = self._agent_factory(request)
        snapshot = await graph.aget_state(self._config(request))
        self._graphs[request.run_id] = graph
        self._requests[request.run_id] = request
        self._history[request.run_id] = []
        if not snapshot.next:
            if snapshot.values.get(PLATFORM_TERMINAL_STATUS_KEY) == "cancelled":
                self._append_event(
                    request,
                    EventType.RUN_CANCELLED,
                    {"status": "cancelled"},
                )
                state = RuntimeState(
                    run_id=request.run_id,
                    status=RunStatus.CANCELLED,
                    data={},
                )
                self._states[request.run_id] = state
                return state
            messages = snapshot.values.get("messages")
            if not isinstance(messages, Sequence) or not messages:
                raise RuntimeRecoveryUnavailable
            last_message = messages[-1]
            if not isinstance(last_message, BaseMessage):
                raise RuntimeRecoveryUnavailable
            output = self._message_text(last_message)
            if output:
                self._append_event(
                    request,
                    EventType.MESSAGE_OUTPUT,
                    {"content": output},
                )
            self._append_event(request, EventType.RUN_COMPLETED, {"status": "completed"})
            state = RuntimeState(
                run_id=request.run_id,
                status=RunStatus.COMPLETED,
                data={"output": output},
            )
            self._states[request.run_id] = state
            return state
        approval = self._approval(snapshot, run_id=request.run_id)
        if approval is None:
            raise RuntimeRecoveryUnavailable
        self._pending_approvals[request.run_id] = approval
        self._append_event(
            request,
            EventType.APPROVAL_REQUIRED,
            {
                "status": "waiting_for_approval",
                "approval_id": str(approval.approval_id),
            },
        )
        state = RuntimeState(run_id=request.run_id, status=status, data={})
        self._states[request.run_id] = state
        return state

    async def _invoke(
        self,
        graph: AgentGraph,
        request: RuntimeStartRequest,
        input_data: Mapping[str, JsonValue],
    ) -> str:
        result = await self._invoke_result(graph, request, input_data)
        return self._output(result)

    async def _invoke_result(
        self,
        graph: AgentGraph,
        request: RuntimeStartRequest,
        input_data: Mapping[str, JsonValue] | Command[Any],
    ) -> Mapping[str, object]:
        graph_input: dict[str, object] | Command[Any]
        if isinstance(input_data, Command):
            graph_input = input_data
        else:
            graph_input = {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(input_data, ensure_ascii=False),
                    }
                ]
            }
        return await graph.ainvoke(
            graph_input,
            self._config(request),
        )

    @staticmethod
    def _output(result: Mapping[str, object]) -> str:
        messages = result.get("messages")
        if not isinstance(messages, Sequence) or not messages:
            return ""
        last_message = messages[-1]
        if not isinstance(last_message, BaseMessage):
            return ""
        return DeepAgentRuntime._message_text(last_message)

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        if isinstance(message.content, str):
            return message.content
        json_content: JsonValue = TypeAdapter(JsonValue).validate_python(message.content)
        return json.dumps(json_content, ensure_ascii=False)

    def stream(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[PlatformEvent]:
        async def iterate() -> AsyncIterator[PlatformEvent]:
            for event in self._required_history(run_id):
                if event.sequence > after_sequence:
                    yield event

        return iterate()

    async def send_message(self, run_id: UUID, message: str) -> None:
        request = self._required_request(run_id)
        output = await self._invoke(
            self._graphs[run_id],
            request,
            {"message": message},
        )
        self._append_event(request, EventType.MESSAGE_OUTPUT, {"content": output})
        self._states[run_id] = RuntimeState(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            data={"output": output},
        )

    async def approve(self, run_id: UUID, approval_id: UUID) -> None:
        expected = self._pending_approvals.get(run_id)
        if expected is None or expected.approval_id != approval_id:
            raise RuntimeControlMismatch
        if self._approval_store is not None:
            try:
                tool_id = self._tool_ids_by_name[expected.tool_name]
            except KeyError as error:
                raise RuntimeRecoveryUnavailable from error
            self._approval_store.grant(
                invocation_id=approval_id,
                run_id=run_id,
                tool_id=tool_id,
                tool_name=expected.tool_name,
                arguments=expected.arguments,
            )
        request = self._required_request(run_id)
        try:
            result = await self._invoke_result(
                self._graphs[run_id],
                request,
                Command(resume={"decisions": [{"type": "approve"}]}),
            )
        finally:
            if self._approval_store is not None:
                self._approval_store.revoke(
                    run_id=run_id,
                    tool_id=tool_id,
                    tool_name=expected.tool_name,
                    arguments=expected.arguments,
                )
        self._pending_approvals.pop(run_id, None)
        if result.get("__interrupt__"):
            snapshot = await self._graphs[run_id].aget_state(self._config(request))
            next_approval = self._approval(snapshot, run_id=run_id)
            if next_approval is None:
                raise RuntimeRecoveryUnavailable
            self._pending_approvals[run_id] = next_approval
            self._append_event(
                request,
                EventType.APPROVAL_REQUIRED,
                {
                    "status": "waiting_for_approval",
                    "approval_id": str(next_approval.approval_id),
                },
            )
            self._states[run_id] = RuntimeState(
                run_id=run_id,
                status=RunStatus.WAITING_FOR_APPROVAL,
                data={},
            )
            return
        output = self._output(result)
        self._append_event(request, EventType.MESSAGE_OUTPUT, {"content": output})
        self._append_event(request, EventType.RUN_COMPLETED, {"status": "completed"})
        self._states[run_id] = RuntimeState(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            data={"output": output},
        )

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None:
        expected = self._pending_approvals.get(run_id)
        if expected is None or expected.approval_id != approval_id:
            raise RuntimeControlMismatch
        request = self._required_request(run_id)
        await self._invoke_result(
            self._graphs[run_id],
            request,
            Command(
                resume={
                    "decisions": [
                        {
                            "type": "reject",
                            "message": reason or "operator rejected",
                        }
                    ]
                },
                update={PLATFORM_TERMINAL_STATUS_KEY: "cancelled"},
            ),
        )
        self._pending_approvals.pop(run_id, None)
        self._append_event(request, EventType.RUN_CANCELLED, {"status": "cancelled"})
        self._states[run_id] = RuntimeState(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            data={},
        )

    async def resume(self, run_id: UUID) -> None:
        del run_id
        raise RuntimeOperationNotSupported

    async def cancel(self, run_id: UUID) -> None:
        request = self._required_request(run_id)
        self._append_event(request, EventType.RUN_CANCELLED, {"status": "cancelled"})
        self._states[run_id] = RuntimeState(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            data={},
        )

    async def get_state(self, run_id: UUID) -> RuntimeState:
        try:
            return self._states[run_id]
        except KeyError as error:
            raise RuntimeRunNotFound from error

    async def get_history(self, run_id: UUID) -> list[PlatformEvent]:
        return list(self._required_history(run_id))

    async def get_artifacts(self, run_id: UUID) -> list[ArtifactReference]:
        self._required_request(run_id)
        return []

    def _append_event(
        self,
        request: RuntimeStartRequest,
        event_type: EventType,
        payload: dict[str, JsonValue],
    ) -> None:
        history = self._required_history(request.run_id)
        history.append(
            PlatformEvent.create(
                tenant_id=request.tenant_id,
                employee_id=request.employee_id,
                run_id=request.run_id,
                sequence=len(history) + 1,
                event_type=event_type,
                payload=payload,
            )
        )

    def _required_request(self, run_id: UUID) -> RuntimeStartRequest:
        try:
            return self._requests[run_id]
        except KeyError as error:
            raise RuntimeRunNotFound from error

    def _required_history(self, run_id: UUID) -> list[PlatformEvent]:
        try:
            return self._history[run_id]
        except KeyError as error:
            raise RuntimeRunNotFound from error

    def pending_approval_id(self, run_id: UUID) -> UUID | None:
        pending = self._pending_approvals.get(run_id)
        return pending.approval_id if pending is not None else None

    @staticmethod
    def _config(request: RuntimeStartRequest) -> dict[str, object]:
        return {"configurable": {"thread_id": request.thread_id}}

    @staticmethod
    def _approval(snapshot: StateSnapshot, *, run_id: UUID) -> PendingToolApproval | None:
        interrupts = [
            item
            for task in snapshot.tasks
            for item in task.interrupts
            if isinstance(item.value, Mapping)
            and isinstance(item.value.get("action_requests"), Sequence)
            and item.value.get("action_requests")
        ]
        if not interrupts:
            return None
        if len(interrupts) != 1:
            raise RuntimeRecoveryUnavailable
        requests = interrupts[0].value["action_requests"]
        if not isinstance(requests, Sequence) or len(requests) != 1:
            raise RuntimeRecoveryUnavailable
        request = requests[0]
        if not isinstance(request, Mapping):
            raise RuntimeRecoveryUnavailable
        tool_name = request.get("name")
        arguments = request.get("args")
        if not isinstance(tool_name, str) or not isinstance(arguments, Mapping):
            raise RuntimeRecoveryUnavailable
        return PendingToolApproval(
            approval_id=uuid5(
                NAMESPACE_URL,
                f"agent-platform:{run_id}:{interrupts[0].id}",
            ),
            tool_name=tool_name,
            arguments={str(key): value for key, value in arguments.items()},
        )
