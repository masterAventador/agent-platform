from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import JsonValue

from agent_platform.platform.runs.errors import InvalidRunTransition


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_FOR_INPUT: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.WAITING_FOR_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

_TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class Run:
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    employee_version: int
    created_by: UUID
    thread_id: str
    input_data: dict[str, JsonValue]
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    idempotency_key: UUID | None = None
    conversation_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        employee_version: int,
        created_by: UUID,
        input_data: dict[str, JsonValue],
        idempotency_key: UUID | None = None,
        conversation_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> "Run":
        run_id = uuid4()
        now = datetime.now(UTC)
        return cls(
            id=run_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            employee_version=employee_version,
            created_by=created_by,
            thread_id=thread_id or str(run_id),
            input_data=input_data,
            status=RunStatus.QUEUED,
            created_at=now,
            updated_at=now,
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
        )

    def transition_to(
        self,
        status: RunStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "Run":
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidRunTransition(f"{self.status.value} -> {status.value}")

        now = datetime.now(UTC)
        return replace(
            self,
            status=status,
            updated_at=now,
            started_at=(
                now if self.started_at is None and status is RunStatus.RUNNING else self.started_at
            ),
            finished_at=now if status in _TERMINAL_STATUSES else None,
            error_code=error_code if status is RunStatus.FAILED else None,
            error_message=error_message if status is RunStatus.FAILED else None,
        )
