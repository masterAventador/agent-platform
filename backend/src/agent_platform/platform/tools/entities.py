import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from agent_platform.platform.tools.errors import InvalidApprovalPolicy, InvalidToolSchema

MAX_TOOL_SCHEMA_BYTES = 64 * 1024
MAX_SYNC_CATALOG_TOOLS = 200


class McpTransport(StrEnum):
    STREAMABLE_HTTP = "streamable_http"
    STDIO = "stdio"


class McpConnectionStatus(StrEnum):
    UNKNOWN = "unknown"
    OK = "ok"
    FAILED = "failed"


class ToolRiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class ToolOrigin(StrEnum):
    MANUAL = "manual"
    DISCOVERED = "discovered"


class ToolApprovalPolicy(StrEnum):
    RISK_BASED = "risk_based"
    ALWAYS = "always"
    NEVER = "never"


def validate_approval_policy(
    risk_level: ToolRiskLevel, approval_policy: ToolApprovalPolicy
) -> None:
    if (
        approval_policy is ToolApprovalPolicy.NEVER
        and risk_level is ToolRiskLevel.DESTRUCTIVE
    ):
        raise InvalidApprovalPolicy


def validate_tool_input_schema(input_schema: dict[str, object]) -> None:
    if not isinstance(input_schema, dict):
        raise InvalidToolSchema
    declared_type = input_schema.get("type")
    if declared_type is not None and declared_type != "object":
        raise InvalidToolSchema
    try:
        serialized = json.dumps(input_schema, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise InvalidToolSchema from None
    if len(serialized.encode("utf-8")) > MAX_TOOL_SCHEMA_BYTES:
        raise InvalidToolSchema


@dataclass(frozen=True, slots=True)
class McpServer:
    id: UUID
    tenant_id: UUID
    name: str
    transport: McpTransport
    endpoint: str | None
    command: str | None
    args: list[str]
    secret_reference: str | None
    enabled: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    connection_status: McpConnectionStatus = McpConnectionStatus.UNKNOWN
    connection_tested_at: datetime | None = None
    connection_error_code: str | None = None
    last_synced_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        transport: McpTransport,
        endpoint: str | None,
        command: str | None,
        args: list[str],
        secret_reference: str | None,
        enabled: bool,
    ) -> "McpServer":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            transport=transport,
            endpoint=endpoint,
            command=command,
            args=args,
            secret_reference=secret_reference,
            enabled=enabled,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def set_enabled(self, enabled: bool) -> "McpServer":
        return replace(self, enabled=enabled, updated_at=datetime.now(UTC))

    def with_settings(
        self,
        *,
        name: str | None = None,
        endpoint: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
    ) -> "McpServer":
        return replace(
            self,
            name=name if name is not None else self.name,
            endpoint=endpoint if endpoint is not None else self.endpoint,
            command=command if command is not None else self.command,
            args=args if args is not None else self.args,
            updated_at=datetime.now(UTC),
        )

    def with_secret_reference(self, secret_reference: str | None) -> "McpServer":
        return replace(
            self, secret_reference=secret_reference, updated_at=datetime.now(UTC)
        )

    def with_connection_result(
        self,
        *,
        status: McpConnectionStatus,
        error_code: str | None,
        tested_at: datetime,
    ) -> "McpServer":
        return replace(
            self,
            connection_status=status,
            connection_error_code=error_code,
            connection_tested_at=tested_at,
        )

    def with_synced_at(self, synced_at: datetime) -> "McpServer":
        return replace(self, last_synced_at=synced_at)


@dataclass(frozen=True, slots=True)
class Tool:
    id: UUID
    tenant_id: UUID
    server_id: UUID
    name: str
    description: str
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel
    enabled: bool
    created_at: datetime
    updated_at: datetime
    origin: ToolOrigin = ToolOrigin.MANUAL
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.RISK_BASED
    upstream_missing: bool = False
    version: int = 1

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        server_id: UUID,
        name: str,
        description: str,
        input_schema: dict[str, object],
        risk_level: ToolRiskLevel,
        enabled: bool,
        origin: ToolOrigin = ToolOrigin.MANUAL,
        approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.RISK_BASED,
    ) -> "Tool":
        validate_tool_input_schema(input_schema)
        validate_approval_policy(risk_level, approval_policy)
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            server_id=server_id,
            name=name,
            description=description,
            input_schema=input_schema,
            risk_level=risk_level,
            enabled=enabled,
            created_at=now,
            updated_at=now,
            origin=origin,
            approval_policy=approval_policy,
        )

    def set_enabled(self, enabled: bool) -> "Tool":
        return replace(self, enabled=enabled, updated_at=datetime.now(UTC))

    def with_definition(
        self,
        *,
        description: str | None = None,
        input_schema: dict[str, object] | None = None,
        risk_level: ToolRiskLevel | None = None,
        approval_policy: ToolApprovalPolicy | None = None,
    ) -> "Tool":
        next_schema = input_schema if input_schema is not None else self.input_schema
        next_risk = risk_level if risk_level is not None else self.risk_level
        next_policy = (
            approval_policy if approval_policy is not None else self.approval_policy
        )
        validate_tool_input_schema(next_schema)
        validate_approval_policy(next_risk, next_policy)
        return replace(
            self,
            description=description if description is not None else self.description,
            input_schema=next_schema,
            risk_level=next_risk,
            approval_policy=next_policy,
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )

    def mark_upstream_missing(self, *, missing: bool, at: datetime) -> "Tool":
        return replace(self, upstream_missing=missing, updated_at=at)

    def snapshot(self, *, change_source: str) -> "ToolVersion":
        return ToolVersion(
            id=uuid4(),
            tenant_id=self.tenant_id,
            tool_id=self.id,
            version=self.version,
            description=self.description,
            input_schema=self.input_schema,
            risk_level=self.risk_level,
            approval_policy=self.approval_policy,
            change_source=change_source,
            created_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class ToolVersion:
    id: UUID
    tenant_id: UUID
    tool_id: UUID
    version: int
    description: str
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel
    approval_policy: ToolApprovalPolicy
    change_source: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ToolReference:
    tool_id: UUID
    employee_id: UUID
    employee_name: str
    relation: str
    version: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class McpSyncRemovedEntry:
    name: str
    referenced: bool


@dataclass(frozen=True, slots=True)
class McpSyncReport:
    id: UUID
    tenant_id: UUID
    server_id: UUID
    occurred_at: datetime
    status: str
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[McpSyncRemovedEntry] = field(default_factory=list)
    unchanged: int = 0
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class McpConnectionResult:
    status: McpConnectionStatus
    tested_at: datetime
    tool_count: int | None = None
    error_code: str | None = None
