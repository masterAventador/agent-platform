import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

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
from agent_platform.runtimes.recovery import (
    RuntimeControlMismatch,
    RuntimeRecoveryUnavailable,
)


class RuntimeRunNotFound(Exception):
    """运行时中不存在该流程任务。"""


class RuntimeOperationNotSupported(Exception):
    """当前流程未启用对应交互操作。"""


class LangGraphAgentGraph(Protocol):
    def astream(
        self,
        input_data: dict[str, object] | Command[Any],
        config: dict[str, object],
        *,
        stream_mode: str,
    ) -> AsyncIterator[Mapping[str, object]]: ...

    async def aget_state(self, config: dict[str, object]) -> StateSnapshot: ...


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
        self._pending_interrupts: dict[UUID, tuple[str, UUID | None]] = {}
        self._active_tasks: dict[UUID, asyncio.Task[RuntimeState]] = {}
        self._cancel_requested: set[UUID] = set()

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        existing = self._active_tasks.get(request.run_id)
        if existing is not None and not existing.done():
            raise RuntimeOperationNotSupported
        self._requests[request.run_id] = request
        self._history[request.run_id] = []
        self._append_event(
            request,
            EventType.RUN_STARTED,
            {"thread_id": request.thread_id},
        )
        try:
            graph = self._graph_factory(request)
            self._graphs[request.run_id] = graph
            return await self._run_drive(graph, request, {"input": request.input_data})
        except asyncio.CancelledError:
            if request.run_id in self._cancel_requested:
                return self._mark_cancelled(request)
            raise
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

    async def recover(
        self,
        request: RuntimeStartRequest,
        status: RunStatus,
    ) -> RuntimeState:
        graph = self._graph_factory(request)
        snapshot = await graph.aget_state(self._config(request))
        interrupts = self._interrupt_values(snapshot)
        if status not in {
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.WAITING_FOR_APPROVAL,
        }:
            raise RuntimeRecoveryUnavailable
        self._graphs[request.run_id] = graph
        self._requests[request.run_id] = request
        self._history[request.run_id] = []
        if not snapshot.next:
            if "output" not in snapshot.values:
                raise RuntimeRecoveryUnavailable
            output: JsonValue = TypeAdapter(JsonValue).validate_python(
                snapshot.values.get("output")
            )
            if (snapshot.metadata or {}).get(
                PLATFORM_TERMINAL_STATUS_KEY
            ) == RunStatus.CANCELLED.value:
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
            if output is not None:
                self._append_event(
                    request,
                    EventType.MESSAGE_OUTPUT,
                    {"content": output},
                )
            self._append_event(
                request,
                EventType.RUN_COMPLETED,
                {"status": "completed"},
            )
            state = RuntimeState(
                run_id=request.run_id,
                status=RunStatus.COMPLETED,
                data={"output": output},
            )
            self._states[request.run_id] = state
            return state
        if not interrupts:
            raise RuntimeRecoveryUnavailable
        pending = self._pending_interrupt(
            interrupts,
            run_id=request.run_id,
            status=status,
        )
        self._pending_interrupts[request.run_id] = pending
        if status is RunStatus.WAITING_FOR_APPROVAL:
            approval_id = pending[1]
            assert approval_id is not None
            self._append_event(
                request,
                EventType.APPROVAL_REQUIRED,
                {
                    "status": "waiting_for_approval",
                    "approval_id": str(approval_id),
                },
            )
        state = RuntimeState(run_id=request.run_id, status=status, data={})
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
        kind, _ = self._required_pending(run_id)
        if kind != "input":
            raise RuntimeControlMismatch
        await self._resume(run_id, {"message": message})

    async def approve(self, run_id: UUID, approval_id: UUID) -> None:
        self._require_approval(run_id, approval_id)
        await self._resume(
            run_id,
            {"action": "approve", "approval_id": str(approval_id)},
        )

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None:
        self._require_approval(run_id, approval_id)
        await self._resume(
            run_id,
            {
                "action": "reject",
                "approval_id": str(approval_id),
                "reason": reason,
            },
            completion_status=RunStatus.CANCELLED,
        )

    async def resume(self, run_id: UUID) -> None:
        kind, _ = self._required_pending(run_id)
        if kind != "input":
            raise RuntimeControlMismatch
        await self._resume(run_id, None)

    async def cancel(self, run_id: UUID) -> None:
        request = self._required_request(run_id)
        self._cancel_requested.add(run_id)
        task = self._active_tasks.get(run_id)
        if task is not None:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._mark_cancelled(request)
        if task is None:
            self._cancel_requested.discard(run_id)

    def _mark_cancelled(self, request: RuntimeStartRequest) -> RuntimeState:
        history = self._required_history(request.run_id)
        if not any(event.type is EventType.RUN_CANCELLED for event in history):
            self._append_event(
                request,
                EventType.RUN_CANCELLED,
                {"status": "cancelled"},
            )
        self._pending_interrupts.pop(request.run_id, None)
        state = RuntimeState(
            run_id=request.run_id,
            status=RunStatus.CANCELLED,
            data={},
        )
        self._states[request.run_id] = state
        return state

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

    async def _resume(
        self,
        run_id: UUID,
        value: JsonValue,
        *,
        completion_status: RunStatus = RunStatus.COMPLETED,
    ) -> None:
        request = self._required_request(run_id)
        graph = self._graphs[run_id]
        await self._run_drive(
            graph,
            request,
            Command(resume=value),
            completion_status=completion_status,
        )

    async def _run_drive(
        self,
        graph: LangGraphAgentGraph,
        request: RuntimeStartRequest,
        input_data: dict[str, object] | Command[Any],
        completion_status: RunStatus = RunStatus.COMPLETED,
    ) -> RuntimeState:
        existing = self._active_tasks.get(request.run_id)
        if existing is not None and not existing.done():
            raise RuntimeOperationNotSupported
        task = asyncio.create_task(
            self._drive(
                graph,
                request,
                input_data,
                completion_status=completion_status,
            )
        )
        self._active_tasks[request.run_id] = task
        try:
            state = await task
            if request.run_id in self._cancel_requested:
                return self._mark_cancelled(request)
            return state
        except asyncio.CancelledError:
            if request.run_id in self._cancel_requested:
                return self._mark_cancelled(request)
            raise
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if self._active_tasks.get(request.run_id) is task:
                self._active_tasks.pop(request.run_id, None)
            self._cancel_requested.discard(request.run_id)

    async def _drive(
        self,
        graph: LangGraphAgentGraph,
        request: RuntimeStartRequest,
        input_data: dict[str, object] | Command[Any],
        completion_status: RunStatus = RunStatus.COMPLETED,
    ) -> RuntimeState:
        output: JsonValue = None
        interrupted = False
        config = self._config(request)
        if completion_status is RunStatus.CANCELLED:
            config["metadata"] = {
                PLATFORM_TERMINAL_STATUS_KEY: RunStatus.CANCELLED.value,
            }
        async for update in graph.astream(
            input_data,
            config,
            stream_mode="updates",
        ):
            for step, state_update in update.items():
                if step == "__interrupt__":
                    interrupted = True
                    continue
                self._append_event(
                    request,
                    EventType.RUN_PROGRESS,
                    {"step": str(step), "status": "completed"},
                )
                if isinstance(state_update, Mapping) and "output" in state_update:
                    output = TypeAdapter(JsonValue).validate_python(state_update["output"])
        if interrupted:
            snapshot = await graph.aget_state(self._config(request))
            interrupts = self._interrupt_values(snapshot)
            waiting_status = (
                RunStatus.WAITING_FOR_APPROVAL
                if any(value.get("kind") == "approval" for _, value in interrupts)
                else RunStatus.WAITING_FOR_INPUT
            )
            pending = self._pending_interrupt(
                interrupts,
                run_id=request.run_id,
                status=waiting_status,
            )
            self._pending_interrupts[request.run_id] = pending
            if waiting_status is RunStatus.WAITING_FOR_APPROVAL:
                approval_id = pending[1]
                assert approval_id is not None
                self._append_event(
                    request,
                    EventType.APPROVAL_REQUIRED,
                    {
                        "status": "waiting_for_approval",
                        "approval_id": str(approval_id),
                    },
                )
            state = RuntimeState(run_id=request.run_id, status=waiting_status, data={})
            self._states[request.run_id] = state
            return state
        self._pending_interrupts.pop(request.run_id, None)
        if request.run_id in self._cancel_requested:
            return self._mark_cancelled(request)
        if completion_status is RunStatus.CANCELLED:
            self._append_event(request, EventType.RUN_CANCELLED, {"status": "cancelled"})
            state = RuntimeState(
                run_id=request.run_id,
                status=RunStatus.CANCELLED,
                data={},
            )
            self._states[request.run_id] = state
            return state
        if output is not None:
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

    @staticmethod
    def _config(request: RuntimeStartRequest) -> dict[str, object]:
        return {"configurable": {"thread_id": request.thread_id}}

    @staticmethod
    def _interrupt_values(
        snapshot: StateSnapshot,
    ) -> list[tuple[str, dict[str, JsonValue]]]:
        values: list[tuple[str, dict[str, JsonValue]]] = []
        for task in snapshot.tasks:
            for item in task.interrupts:
                if isinstance(item.value, Mapping):
                    values.append(
                        (
                            str(item.id),
                            TypeAdapter(dict[str, JsonValue]).validate_python(item.value),
                        )
                    )
        return values

    @staticmethod
    def _pending_interrupt(
        interrupts: list[tuple[str, dict[str, JsonValue]]],
        *,
        run_id: UUID,
        status: RunStatus,
    ) -> tuple[str, UUID | None]:
        if len(interrupts) != 1:
            raise RuntimeRecoveryUnavailable
        occurrence_id, value = interrupts[0]
        if status is RunStatus.WAITING_FOR_APPROVAL:
            if value.get("kind") == "approval":
                try:
                    TypeAdapter(UUID).validate_python(value.get("approval_id"))
                except ValueError:
                    raise RuntimeRecoveryUnavailable from None
                return "approval", uuid5(
                    NAMESPACE_URL,
                    f"agent-platform:{run_id}:{occurrence_id}",
                )
            raise RuntimeRecoveryUnavailable
        if status is RunStatus.WAITING_FOR_INPUT:
            if value.get("kind") == "input":
                return "input", None
            raise RuntimeRecoveryUnavailable
        raise RuntimeRecoveryUnavailable

    def _required_pending(self, run_id: UUID) -> tuple[str, UUID | None]:
        try:
            return self._pending_interrupts[run_id]
        except KeyError as error:
            raise RuntimeControlMismatch from error

    def pending_approval_id(self, run_id: UUID) -> UUID | None:
        kind, approval_id = self._required_pending(run_id)
        return approval_id if kind == "approval" else None

    def _require_approval(self, run_id: UUID, approval_id: UUID) -> None:
        kind, expected_id = self._required_pending(run_id)
        if kind != "approval" or expected_id != approval_id:
            raise RuntimeControlMismatch

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
