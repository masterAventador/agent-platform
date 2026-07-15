from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.artifacts import ArtifactBackedRuntime
from agent_platform.runtimes.base import ArtifactReference, RuntimeStartRequest, RuntimeState


class FakeRuntime:
    def __init__(
        self,
        request: RuntimeStartRequest,
        *,
        history: list[PlatformEvent] | None = None,
    ) -> None:
        self.request = request
        self.history = history or []

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        self.request = request
        return RuntimeState(run_id=request.run_id, status=RunStatus.COMPLETED, data={})

    def stream(self, run_id: UUID, *, after_sequence: int = 0) -> AsyncIterator[PlatformEvent]:
        del run_id, after_sequence

        async def empty() -> AsyncIterator[PlatformEvent]:
            if False:
                yield

        return empty()

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
        return RuntimeState(run_id=run_id, status=RunStatus.COMPLETED, data={})

    async def get_history(self, run_id: UUID) -> list[PlatformEvent]:
        del run_id
        return self.history

    async def get_artifacts(self, run_id: UUID) -> list[ArtifactReference]:
        del run_id
        return []


@pytest.mark.asyncio
async def test_get_artifacts_uses_persistent_catalog_instead_of_runtime_memory() -> None:
    run_id = uuid4()
    request = RuntimeStartRequest(
        run_id=run_id,
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id="thread-1",
        employee_definition={},
        input_data={},
    )
    expected = [
        ArtifactReference(
            artifact_id=uuid4(),
            name="result.txt",
            media_type="text/plain",
            size_bytes=4,
        )
    ]

    async def catalog(requested_run_id: UUID) -> list[ArtifactReference]:
        assert requested_run_id == run_id
        return expected

    runtime = ArtifactBackedRuntime(runtime=FakeRuntime(request), artifact_catalog=catalog)

    assert await runtime.get_artifacts(run_id) == expected


@pytest.mark.asyncio
async def test_artifact_event_is_visible_before_terminal_event() -> None:
    tenant_id, employee_id, run_id = uuid4(), uuid4(), uuid4()
    request = RuntimeStartRequest(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=uuid4(),
        employee_id=employee_id,
        thread_id="thread-1",
        employee_definition={},
        input_data={},
    )
    started = PlatformEvent.create(
        tenant_id=tenant_id,
        employee_id=employee_id,
        run_id=run_id,
        sequence=1,
        event_type=EventType.RUN_STARTED,
        payload={},
    )
    completed = PlatformEvent.create(
        tenant_id=tenant_id,
        employee_id=employee_id,
        run_id=run_id,
        sequence=3,
        event_type=EventType.RUN_COMPLETED,
        payload={},
    )
    artifact_created = PlatformEvent.create(
        tenant_id=tenant_id,
        employee_id=employee_id,
        run_id=run_id,
        sequence=1,
        event_type=EventType.ARTIFACT_CREATED,
        payload={"artifact_id": str(uuid4()), "name": "result.txt"},
    )
    runtime = ArtifactBackedRuntime(
        runtime=FakeRuntime(request, history=[started, completed]),
        artifact_catalog=lambda _: _empty_catalog(),
        artifact_events=lambda: [artifact_created],
    )

    history = await runtime.get_history(run_id)

    assert [event.type for event in history] == [
        EventType.RUN_STARTED,
        EventType.ARTIFACT_CREATED,
        EventType.RUN_COMPLETED,
    ]
    assert [event.sequence for event in history] == [1, 2, 3]


async def _empty_catalog() -> list[ArtifactReference]:
    return []
