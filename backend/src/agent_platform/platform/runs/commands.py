from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import JsonValue


class RunCommandAction(StrEnum):
    START = "start"
    RESUME = "resume"
    CANCEL = "cancel"
    MESSAGE = "message"
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RunCommand:
    id: UUID
    run_id: UUID
    tenant_id: UUID
    action: RunCommandAction
    payload: dict[str, JsonValue]
    created_at: datetime
    dispatched_at: datetime | None = None
    processed_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID,
        tenant_id: UUID,
        action: RunCommandAction,
        payload: dict[str, JsonValue] | None = None,
    ) -> "RunCommand":
        return cls(
            id=uuid4(),
            run_id=run_id,
            tenant_id=tenant_id,
            action=action,
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
