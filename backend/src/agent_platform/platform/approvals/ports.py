"""审批服务对存储与审计的端口协议（平台层不依赖具体基础设施实现）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from agent_platform.platform.approvals.entities import Approval, ApprovalStatus
from agent_platform.platform.runs.commands import RunCommand
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.runs.events import PlatformEvent


class ApprovalStore(Protocol):
    async def add_idempotent(self, approval: Approval) -> Approval: ...

    async def get(self, *, tenant_id: UUID, approval_id: UUID) -> Approval | None: ...

    async def get_active_for_invocation(
        self, *, tenant_id: UUID, run_id: UUID, invocation_id: UUID
    ) -> Approval | None: ...

    async def get_latest_for_invocation(
        self, *, tenant_id: UUID, run_id: UUID, invocation_id: UUID
    ) -> Approval | None: ...

    async def list(
        self,
        *,
        tenant_id: UUID,
        statuses: tuple[ApprovalStatus, ...] | None = None,
        assignee_id: UUID | None = None,
        visible_to: UUID | None = None,
        include_unassigned: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Approval], int]: ...

    async def list_pending_for_run(
        self, *, tenant_id: UUID, run_id: UUID
    ) -> Sequence[Approval]: ...

    async def list_overdue_pending(
        self, *, now: datetime, limit: int
    ) -> Sequence[Approval]: ...

    async def update_with_cas(
        self, approval: Approval, *, expected_revision: int
    ) -> bool: ...


class RunStore(Protocol):
    async def get_for_update(self, *, tenant_id: UUID, run_id: UUID) -> Run | None: ...


class RunCommandStore(Protocol):
    async def add(self, command: RunCommand) -> None: ...


class RunEventStore(Protocol):
    async def append(self, event: PlatformEvent) -> None: ...

    async def next_sequence(self, *, run_id: UUID) -> int: ...


class AuditSink(Protocol):
    async def __call__(
        self,
        *,
        tenant_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        metadata: Mapping[str, JsonValue],
    ) -> object: ...
