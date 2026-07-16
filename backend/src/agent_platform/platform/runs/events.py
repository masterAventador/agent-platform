from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID, uuid4

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_platform.observability.spans import PlatformSpan, with_trace_correlation


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_PROGRESS = "run.progress"
    MESSAGE_OUTPUT = "message.output"
    KNOWLEDGE_RETRIEVED = "knowledge.retrieved"
    PLAN_UPDATED = "plan.updated"
    SKILL_LOADED = "skill.loaded"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    SUBAGENT_STARTED = "subagent.started"
    SUBAGENT_COMPLETED = "subagent.completed"
    APPROVAL_REQUIRED = "approval.required"
    ARTIFACT_CREATED = "artifact.created"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


class PlatformEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    event_version: Literal["1.0"] = "1.0"
    tenant_id: UUID
    employee_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    type: EventType
    occurred_at: datetime
    payload: dict[str, JsonValue]

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        run_id: UUID,
        sequence: int,
        event_type: EventType,
        payload: dict[str, JsonValue],
    ) -> "PlatformEvent":
        correlated_payload = with_trace_correlation(
            payload,
            cast(PlatformSpan, trace.get_current_span()),
        )
        return cls(
            event_id=uuid4(),
            tenant_id=tenant_id,
            employee_id=employee_id,
            run_id=run_id,
            sequence=sequence,
            type=event_type,
            occurred_at=datetime.now(UTC),
            payload=correlated_payload,
        )
