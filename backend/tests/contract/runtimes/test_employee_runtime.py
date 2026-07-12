from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.runs.events import PlatformEvent
from agent_platform.runtimes.base import (
    ArtifactReference,
    EmployeeRuntime,
    RuntimeStartRequest,
    RuntimeState,
)


class ContractRuntime:
    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        return RuntimeState(run_id=request.run_id, status="running", data={})

    def stream(self, run_id: UUID, *, after_sequence: int = 0) -> AsyncIterator[PlatformEvent]:
        del run_id, after_sequence
        return self._empty_stream()

    async def send_message(self, run_id: UUID, message: str) -> None:
        del run_id, message

    async def approve(self, run_id: UUID, approval_id: UUID) -> None:
        del run_id, approval_id

    async def reject(self, run_id: UUID, approval_id: UUID, reason: str | None = None) -> None:
        del run_id, approval_id, reason

    async def resume(self, run_id: UUID) -> None:
        del run_id

    async def cancel(self, run_id: UUID) -> None:
        del run_id

    async def get_state(self, run_id: UUID) -> RuntimeState:
        return RuntimeState(run_id=run_id, status="running", data={})

    async def get_history(self, run_id: UUID) -> list[PlatformEvent]:
        del run_id
        return []

    async def get_artifacts(self, run_id: UUID) -> list[ArtifactReference]:
        del run_id
        return []

    @staticmethod
    async def _empty_stream() -> AsyncIterator[PlatformEvent]:
        if False:
            yield PlatformEvent.model_construct()


@pytest.mark.asyncio
async def test_runtime_protocol_covers_platform_operations() -> None:
    runtime = ContractRuntime()
    assert isinstance(runtime, EmployeeRuntime)

    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="thread-1",
        employee_definition={"name": "研究助理"},
        input_data={"topic": "Agent"},
    )
    state = await runtime.start(request)
    assert state.run_id == request.run_id
    assert [event async for event in runtime.stream(request.run_id)] == []
