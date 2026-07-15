from __future__ import annotations

import json
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

LOCAL_EXECUTOR_PROTOCOL_VERSION = "1.0"
LOCAL_EXECUTOR_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_DEADLINE_SEMANTIC_RULE = "deadline_at must be later than sent_at"
_SENSITIVE_EXTENSION_SEMANTIC_RULE = "extension keys must not name sensitive data"
_SAFE_MESSAGE_SEMANTIC_RULE = "safe_message must not contain credential or local path markers"
_SAFE_CONTROL_EXTENSION_SEMANTIC_RULE = (
    "control event string extensions must not contain credential or local path markers"
)
_SENSITIVE_FIELD_NAMES = (
    "access_token",
    "api_key",
    "api_token",
    "authorization",
    "cookie",
    "credential",
    "file_path",
    "object_key",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_cookie",
    "signed_url",
    "token",
)
_SENSITIVE_FIELD_PATTERN = "|".join(
    re.escape(name) for name in sorted(_SENSITIVE_FIELD_NAMES, key=len, reverse=True)
)
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    rf"(?<![a-z0-9_])[\"']?(?:{_SENSITIVE_FIELD_PATTERN})[\"']?\s*[:=]\s*"
    r"(?P<rhs>.*?)(?=(?:\s+and\s+[a-z_][a-z0-9_]*\s*:)|[,;&}\r\n]|$)",
    re.IGNORECASE,
)
_SAFE_CREDENTIAL_ASSIGNMENT_VALUES = frozenset(
    {
        "array",
        "boolean",
        "disabled",
        "disabled by administrator",
        "integer",
        "missing",
        "none",
        "not configured",
        "not set",
        "null",
        "number",
        "object",
        "redacted",
        "string",
        "unavailable",
    }
)
_FILE_URI_PATTERN = re.compile(r"\bfile://", re.IGNORECASE)
_PRIVATE_POSIX_PATH_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:/users|/home|/root|/tmp|/var/folders)(?:/|$)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![a-z0-9])[a-z]:[\\/]",
    re.IGNORECASE,
)
_INLINE_DATA_URI_PATTERN = re.compile(
    r"\bdata:[a-z0-9.+-]+/[a-z0-9.+-]+(?:;[a-z0-9.+-]+(?:=[^,;\s]+)?)*,",
    re.IGNORECASE,
)
_INLINE_BASE64_PATTERN = re.compile(
    r"(?:^|[\s;,])base64,[a-z0-9+/]{4,}={0,2}(?:$|[\s;,])",
    re.IGNORECASE,
)
_SENSITIVE_EXTENSION_SEGMENTS = frozenset(_SENSITIVE_FIELD_NAMES)
_SENSITIVE_EXTENSION_ATOM_PATTERN = re.compile(rf"(?:^|_)(?:{_SENSITIVE_FIELD_PATTERN})(?:_|$)")

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


def _contains_sensitive_assignment(value: str) -> bool:
    for match in _CREDENTIAL_ASSIGNMENT_PATTERN.finditer(value):
        assignment_value = match.group("rhs").strip().strip("\"'").rstrip(".").strip()
        if assignment_value.casefold() not in _SAFE_CREDENTIAL_ASSIGNMENT_VALUES:
            return True
    return False


def _contains_unsafe_text_marker(value: str) -> bool:
    return (
        "bearer " in value.casefold()
        or _contains_sensitive_assignment(value)
        or _FILE_URI_PATTERN.search(value) is not None
        or _PRIVATE_POSIX_PATH_PATTERN.search(value) is not None
        or _WINDOWS_ABSOLUTE_PATH_PATTERN.search(value) is not None
        or _INLINE_DATA_URI_PATTERN.search(value) is not None
        or _INLINE_BASE64_PATTERN.search(value) is not None
    )


def _validate_safe_display_message(value: str) -> str:
    if _contains_unsafe_text_marker(value):
        raise ValueError(_SAFE_MESSAGE_SEMANTIC_RULE)
    return value


def _validate_safe_control_extension_string(value: str) -> str:
    if _contains_unsafe_text_marker(value) or _is_json_container(value):
        raise ValueError(_SAFE_CONTROL_EXTENSION_SEMANTIC_RULE)
    return value


def _is_json_container(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict | list)


_SafeDisplayMessage = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=r"^[^\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+$",
    ),
    AfterValidator(_validate_safe_display_message),
]
_SafeControlExtensionString = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=r"^[^\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+$",
    ),
    AfterValidator(_validate_safe_control_extension_string),
]
_FiniteControlFloat = Annotated[StrictFloat, Field(allow_inf_nan=False)]
_ControlExtensionValue = (
    _SafeControlExtensionString | StrictInt | _FiniteControlFloat | StrictBool | None
)


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


class _LocalExecutorMessageBase[ExtensionValueT](_ProtocolModel):
    protocol_version: Literal["1.0"]
    message_id: UUID
    sent_at: AwareDatetime
    identity: LocalTaskIdentity
    governance: GovernanceReferences
    extensions: dict[_ExtensionKey, ExtensionValueT] = Field(
        default_factory=dict,
        json_schema_extra={"additionalProperties": False},
    )

    @field_validator("extensions")
    @classmethod
    def reject_sensitive_extension_keys(
        cls,
        extensions: dict[str, ExtensionValueT],
    ) -> dict[str, ExtensionValueT]:
        for key in extensions:
            segments = set(re.split(r"[.-]", key))
            if segments & _SENSITIVE_EXTENSION_SEGMENTS or any(
                _SENSITIVE_EXTENSION_ATOM_PATTERN.search(segment) for segment in segments
            ):
                raise ValueError(_SENSITIVE_EXTENSION_SEMANTIC_RULE)
        return extensions


class LocalTaskRequest(_LocalExecutorMessageBase[JsonValue]):
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


class LocalTaskCancel(_LocalExecutorMessageBase[JsonValue]):
    message_type: Literal["task.cancel"]
    idempotency_key: _IdempotencyKey
    reason_code: Literal[
        "user_requested",
        "entitlement_revoked",
        "emergency_stop",
        "deadline_expired",
    ]


class LocalTaskResponse(_LocalExecutorMessageBase[JsonValue]):
    message_type: Literal["task.response"]
    executor_id: UUID
    status: Literal["accepted", "running", "completed", "cancelled"]
    result: dict[str, JsonValue]
    artifact_refs: tuple[
        LocalArtifactReference[Literal["output", "evidence"]],
        ...,
    ]


class LocalTaskError(_LocalExecutorMessageBase[JsonValue]):
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
    safe_message: _SafeDisplayMessage
    retryable: bool
    retry_after_ms: Annotated[int, Field(ge=1, le=86_400_000)] | None = None

    @model_validator(mode="after")
    def reject_retry_delay_for_terminal_error(self) -> LocalTaskError:
        if not self.retryable and self.retry_after_ms is not None:
            raise ValueError("retry_after_ms requires a retryable error")
        return self


class _LocalExecutorControlEventBase(_LocalExecutorMessageBase[_ControlExtensionValue]):
    executor_id: UUID
    event_sequence: Annotated[StrictInt, Field(ge=1)]
    artifact_refs: tuple[LocalArtifactReference[Literal["evidence"]], ...]


class LocalStepProgress(_LocalExecutorControlEventBase):
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "started"}},
                        "required": ["status"],
                    },
                    "then": {"properties": {"progress_percent": {"const": 0}}},
                },
                {
                    "if": {
                        "properties": {"status": {"const": "completed"}},
                        "required": ["status"],
                    },
                    "then": {"properties": {"progress_percent": {"const": 100}}},
                },
            ]
        }
    )

    message_type: Literal["step.progress"]
    step_id: UUID
    step_code: _TaskType
    status: Literal[
        "started",
        "in_progress",
        "waiting_for_human",
        "completed",
        "failed",
        "cancelled",
    ]
    progress_percent: Annotated[StrictInt, Field(ge=0, le=100)]
    safe_message: _SafeDisplayMessage

    @model_validator(mode="after")
    def validate_progress_boundary(self) -> LocalStepProgress:
        if self.status == "started" and self.progress_percent != 0:
            raise ValueError("started progress_percent must be 0")
        if self.status == "completed" and self.progress_percent != 100:
            raise ValueError("completed progress_percent must be 100")
        return self


class LocalHandoffRequested(_LocalExecutorControlEventBase):
    message_type: Literal["handoff.requested"]
    handoff_id: UUID
    step_id: UUID
    status: Literal["required"]
    reason_code: Literal[
        "captcha_required",
        "qr_code_expired",
        "risk_control",
        "element_drift",
        "permission_required",
        "unknown",
    ]
    safe_message: _SafeDisplayMessage


class LocalDiagnosticEvent(_LocalExecutorControlEventBase):
    message_type: Literal["diagnostic.event"]
    diagnostic_id: UUID
    step_id: UUID | None
    severity: Literal["info", "warning", "error"]
    code: _ErrorCode
    safe_message: _SafeDisplayMessage


LocalControlEvent = LocalStepProgress | LocalHandoffRequested | LocalDiagnosticEvent


_LocalExecutorMessageUnion = Annotated[
    LocalTaskRequest
    | LocalTaskCancel
    | LocalTaskResponse
    | LocalTaskError
    | LocalStepProgress
    | LocalHandoffRequested
    | LocalDiagnosticEvent,
    Field(discriminator="message_type"),
]


class LocalExecutorMessage(RootModel[_LocalExecutorMessageUnion]):
    """Discriminated, JSON-serializable Social Operations local executor frame."""

    model_config = ConfigDict(
        json_schema_extra={
            "$schema": LOCAL_EXECUTOR_SCHEMA_DIALECT,
            "x-semantic-validation-required": [
                _SENSITIVE_EXTENSION_SEMANTIC_RULE,
                _SAFE_MESSAGE_SEMANTIC_RULE,
                _SAFE_CONTROL_EXTENSION_SEMANTIC_RULE,
            ],
        }
    )
