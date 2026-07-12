from collections.abc import AsyncIterator, Callable, Mapping
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import (
    ArtifactReference,
    RuntimeStartRequest,
    RuntimeState,
)


class RuntimeRunNotFound(Exception):
    """运行时中不存在该流程任务。"""


class RuntimeOperationNotSupported(Exception):
    """当前流程未启用对应交互操作。"""


class LangGraphAgentGraph(Protocol):
    def astream(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
        *,
        stream_mode: str,
    ) -> AsyncIterator[Mapping[str, object]]: ...


class LangGraphRuntime:
    def __init__(
        self,
        *,
        graph_factory: Callable[[RuntimeStartRequest], LangGraphAgentGraph],
    ) -> None:
        self._graph_factory = graph_factory
        self._graphs: dict[UUID, LangGraphAgentGraph] = {}
        self._requests: dict[UUID, RuntimeStartRequest] = {}
        self._states: dict[UUID, RuntimeState] = {}
        self._history: dict[UUID, list[PlatformEvent]] = {}

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self._requests[request.run_id] = request
        self._history[request.run_id] = []
        self._append_event(
            request,
            EventType.RUN_STARTED,
            {"thread_id": request.thread_id},
        )
        output: JsonValue = None

        try:
            graph = self._graph_factory(request)
            self._graphs[request.run_id] = graph
            async for update in graph.astream(
                {"input": request.input_data},
                {"configurable": {"thread_id": request.thread_id}},
                stream_mode="updates",
            ):
                for step, state_update in update.items():
                    self._append_event(
                        request,
                        EventType.RUN_PROGRESS,
                        {"step": str(step), "status": "completed"},
                    )
                    if isinstance(state_update, Mapping) and "output" in state_update:
                        output = TypeAdapter(JsonValue).validate_python(
                            state_update["output"]
                        )
        except Exception as error:
            self._append_event(
                request,
                EventType.RUN_FAILED,
                {
                    "code": "langgraph_execution_failed",
                    "error_type": type(error).__name__,
                },
            )
            state = RuntimeState(
                run_id=request.run_id,
                status=RunStatus.FAILED,
                data={"error_code": "langgraph_execution_failed"},
            )
            self._states[request.run_id] = state
            return state

        self._append_event(request, EventType.RUN_COMPLETED, {"status": "completed"})
        state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.COMPLETED,
            data={"output": output},
        )
        self._states[request.run_id] = state
        return state

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
        del run_id, message
        raise RuntimeOperationNotSupported

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
