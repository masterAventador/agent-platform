from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agent_platform.platform.tools.entities import ToolRiskLevel

ToolRisk = ToolRiskLevel


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class AuditEventType(StrEnum):
    STARTED = "tool.started"
    COMPLETED = "tool.completed"
    REJECTED = "tool.rejected"
    APPROVAL_REQUIRED = "approval.required"


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    tenant_id: UUID
    run_id: UUID
    employee_id: UUID
    user_id: UUID
    tool_id: UUID
    tool_name: str
    arguments: Mapping[str, object]
    invocation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tenant_id: UUID
    tool_id: UUID
    server_id: UUID
    name: str
    risk: ToolRiskLevel
    enabled: bool = True
    server_enabled: bool = True
    credential_references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyContext:
    allowed_tool_ids: frozenset[UUID]
    approval_granted: bool = False


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    decision: PolicyDecision
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ArgumentSummary:
    keys: tuple[str, ...]
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ToolAuditEvent:
    event_type: AuditEventType
    occurred_at: datetime
    tenant_id: UUID
    run_id: UUID
    employee_id: UUID
    user_id: UUID
    tool_id: UUID
    tool_name: str
    risk: ToolRiskLevel | None
    argument_summary: ArgumentSummary
    reason: str | None = None
    succeeded: bool | None = None
    invocation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ToolInvocationOutcome:
    decision: PolicyDecision
    reason: str | None = None
    result: object | None = None
