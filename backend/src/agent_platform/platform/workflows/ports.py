from __future__ import annotations

from typing import Protocol
from uuid import UUID

from agent_platform.platform.workflows.entities import Workflow, WorkflowVersion


class WorkflowRepository(Protocol):
    async def add(self, workflow: Workflow) -> None: ...

    async def update(self, workflow: Workflow) -> None: ...

    async def get(self, *, tenant_id: UUID, workflow_id: UUID) -> Workflow | None: ...

    async def list(self, *, tenant_id: UUID) -> list[Workflow]: ...


class WorkflowVersionRepository(Protocol):
    async def add(self, version: WorkflowVersion) -> None: ...

    async def get(
        self, *, tenant_id: UUID, workflow_id: UUID, version: int
    ) -> WorkflowVersion | None: ...

    async def list(self, *, tenant_id: UUID, workflow_id: UUID) -> list[WorkflowVersion]: ...
