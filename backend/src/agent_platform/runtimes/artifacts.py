from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

from agent_platform.platform.runs.events import PlatformEvent
from agent_platform.runtimes.base import (
    ArtifactReference,
    EmployeeRuntime,
    RuntimeStartRequest,
    RuntimeState,
)

ArtifactCatalog = Callable[[UUID], Awaitable[list[ArtifactReference]]]
ArtifactEvents = Callable[[], list[PlatformEvent]]


class ArtifactBackedRuntime:
    """Delegates execution while making persistent artifact metadata the only catalog."""

    def __init__(
        self,
        *,
        runtime: EmployeeRuntime,
        artifact_catalog: ArtifactCatalog,
        artifact_events: ArtifactEvents | None = None,
    ) -> None:
        self._runtime = runtime
        self._artifact_catalog = artifact_catalog
        self._artifact_events = artifact_events or list

    async def start(self, request: RuntimeStartRequest) -> RuntimeState:
        return await self._runtime.start(request)

    def stream(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[PlatformEvent]:
        return self._runtime.stream(run_id, after_sequence=after_sequence)

    async def send_message(self, run_id: UUID, message: str) -> None:
        await self._runtime.send_message(run_id, message)

    async def approve(self, run_id: UUID, approval_id: UUID) -> None:
        await self._runtime.approve(run_id, approval_id)

    async def reject(
        self,
        run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> None:
        await self._runtime.reject(run_id, approval_id, reason)

    async def resume(self, run_id: UUID) -> None:
        await self._runtime.resume(run_id)

    async def cancel(self, run_id: UUID) -> None:
        await self._runtime.cancel(run_id)

    async def get_state(self, run_id: UUID) -> RuntimeState:
        return await self._runtime.get_state(run_id)

    async def get_history(self, run_id: UUID) -> list[PlatformEvent]:
        history = await self._runtime.get_history(run_id)
        events = self._artifact_events()
        if not events:
            return history
        terminal_index = next(
            (
                index
                for index, event in enumerate(history)
                if event.type.value in {"run.completed", "run.failed", "run.cancelled"}
            ),
            len(history),
        )
        merged = [*history[:terminal_index], *events, *history[terminal_index:]]
        return [
            event.model_copy(update={"sequence": sequence})
            for sequence, event in enumerate(merged, start=1)
        ]

    async def get_artifacts(self, run_id: UUID) -> list[ArtifactReference]:
        return await self._artifact_catalog(run_id)
