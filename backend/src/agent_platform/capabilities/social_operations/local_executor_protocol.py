from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    model_validator,
)

LOCAL_EXECUTOR_PROTOCOL_VERSION = "1.0"
LOCAL_EXECUTOR_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_DEADLINE_SEMANTIC_RULE = "deadline_at must be later than sent_at"

_IdempotencyKey = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
_TaskType = Annotated[
    str,
    Field(pattern=r"^social\.[a-z0-9]+(?:[._-][a-z0-9]+)*$"),
]
_ErrorCode = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"),
]
_ExtensionKey = Annotated[
    str,
    Field(pattern=r"^social\.[a-z0-9_]+(?:[.-][a-z0-9_]+)*$"),
]


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LocalTaskIdentity(_ProtocolModel):
    """Stable Core identities referenced by, but not redefined in, this protocol."""

    task_id: UUID
    correlation_id: UUID
    tenant_id: UUID
    capability_id: Literal["social-operations"]
    target_device_id: UUID


class GovernanceReferences(_ProtocolModel):
    """Opaque links to Core approval and audit records."""

    audit_correlation_id: UUID
    approval_id: UUID | None


class LocalArtifactReference[ArtifactUsageT: str](_ProtocolModel):
    """A reference to a Core Artifact; metadata remains owned by Core."""

    artifact_id: UUID
    usage: ArtifactUsageT


class _LocalExecutorMessageBase(_ProtocolModel):
    protocol_version: Literal["1.0"]
    message_id: UUID
    sent_at: AwareDatetime
    identity: LocalTaskIdentity
    governance: GovernanceReferences
    extensions: dict[_ExtensionKey, JsonValue] = Field(
        default_factory=dict,
        json_schema_extra={"additionalProperties": False},
    )


class LocalTaskRequest(_LocalExecutorMessageBase):
    model_config = ConfigDict(
        json_schema_extra={"x-semantic-validation-required": [_DEADLINE_SEMANTIC_RULE]}
    )

    message_type: Literal["task.request"]
    idempotency_key: _IdempotencyKey
    deadline_at: AwareDatetime
    task_type: _TaskType
    input: dict[str, JsonValue]
    artifact_refs: tuple[LocalArtifactReference[Literal["input"]], ...]

    @model_validator(mode="after")
    def validate_request_boundaries(self) -> LocalTaskRequest:
        if self.deadline_at <= self.sent_at:
            raise ValueError(_DEADLINE_SEMANTIC_RULE)
        return self


class LocalTaskCancel(_LocalExecutorMessageBase):
    message_type: Literal["task.cancel"]
    idempotency_key: _IdempotencyKey
    reason_code: Literal[
        "user_requested",
        "entitlement_revoked",
        "emergency_stop",
        "deadline_expired",
    ]


class LocalTaskResponse(_LocalExecutorMessageBase):
    message_type: Literal["task.response"]
    executor_id: UUID
    status: Literal["accepted", "running", "completed", "cancelled"]
    result: dict[str, JsonValue]
    artifact_refs: tuple[
        LocalArtifactReference[Literal["output", "evidence"]],
        ...,
    ]


class LocalTaskError(_LocalExecutorMessageBase):
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"retryable": {"const": False}},
                        "required": ["retryable"],
                    },
                    "then": {"properties": {"retry_after_ms": {"type": "null"}}},
                }
            ]
        }
    )

    message_type: Literal["task.error"]
    executor_id: UUID
    category: Literal[
        "invalid_request",
        "authorization",
        "deadline_exceeded",
        "cancelled",
        "unavailable",
        "execution_failed",
        "internal",
    ]
    code: _ErrorCode
    safe_message: Annotated[str, Field(min_length=1, max_length=512)]
    retryable: bool
    retry_after_ms: Annotated[int, Field(ge=1, le=86_400_000)] | None = None

    @model_validator(mode="after")
    def reject_retry_delay_for_terminal_error(self) -> LocalTaskError:
        if not self.retryable and self.retry_after_ms is not None:
            raise ValueError("retry_after_ms requires a retryable error")
        return self


_LocalExecutorMessageUnion = Annotated[
    LocalTaskRequest | LocalTaskCancel | LocalTaskResponse | LocalTaskError,
    Field(discriminator="message_type"),
]


class LocalExecutorMessage(RootModel[_LocalExecutorMessageUnion]):
    """Discriminated, JSON-serializable Social Operations local executor frame."""

    model_config = ConfigDict(json_schema_extra={"$schema": LOCAL_EXECUTOR_SCHEMA_DIALECT})
