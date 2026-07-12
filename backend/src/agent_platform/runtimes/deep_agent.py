import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, Protocol, cast
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import BackendProtocol
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from pydantic import JsonValue, TypeAdapter

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import (
    ArtifactReference,
    RuntimeStartRequest,
    RuntimeState,
)


class RuntimeRunNotFound(Exception):
    """运行时中不存在该任务。"""


class RuntimeOperationNotSupported(Exception):
    """当前自主员工没有可处理的对应操作。"""


class AgentGraph(Protocol):
    async def ainvoke(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
    ) -> Mapping[str, object]: ...


class DeepAgentFactory:
    """仅通过 Deep Agents 公开工厂创建自主员工图。"""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        tools: Sequence[BaseTool | Callable[..., Any] | dict[str, Any]],
        backend: BackendProtocol | None = None,
        agent_builder: Callable[..., object] = create_deep_agent,
    ) -> None:
        self._model = model
        self._tools = tools
        self._backend = backend
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
        )
        return cast(AgentGraph, graph)


class DeepAgentRuntime:
    def __init__(
        self,
        *,
        agent_factory: Callable[[RuntimeStartRequest], AgentGraph],
    ) -> None:
        self._agent_factory = agent_factory
        self._graphs: dict[UUID, AgentGraph] = {}
        self._requests: dict[UUID, RuntimeStartRequest] = {}
        self._states: dict[UUID, RuntimeState] = {}
        self._history: dict[UUID, list[PlatformEvent]] = {}

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        graph = self._agent_factory(request)
        self._graphs[request.run_id] = graph
        self._requests[request.run_id] = request
        self._history[request.run_id] = []
        self._append_event(
            request,
            EventType.RUN_STARTED,
            {"thread_id": request.thread_id},
        )

        try:
            output = await self._invoke(graph, request, request.input_data)
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

    async def _invoke(
        self,
        graph: AgentGraph,
        request: RuntimeStartRequest,
        input_data: Mapping[str, JsonValue],
    ) -> str:
        result = await graph.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(input_data, ensure_ascii=False),
                    }
                ]
            },
            {"configurable": {"thread_id": request.thread_id}},
        )
        messages = result.get("messages")
        if not isinstance(messages, Sequence) or not messages:
            return ""
        last_message = messages[-1]
        if not isinstance(last_message, BaseMessage):
            return ""
        return self._message_text(last_message)

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
        del run_id, approval_id
        raise RuntimeOperationNotSupported

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None:
        del run_id, approval_id, reason
        raise RuntimeOperationNotSupported

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
