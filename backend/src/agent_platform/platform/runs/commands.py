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
    # 会话自动续跑意图：挂在当前活跃 Run 上，创建时即标记已分发、不进入执行队列，
    # 由 Worker 在该会话轮次终态结算时消费并派生下一轮 Run。
    FOLLOWUP = "followup"


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
        dispatched_at: datetime | None = None,
    ) -> "RunCommand":
        return cls(
            id=uuid4(),
            run_id=run_id,
            tenant_id=tenant_id,
            action=action,
            payload=payload or {},
            created_at=datetime.now(UTC),
            dispatched_at=dispatched_at,
        )
