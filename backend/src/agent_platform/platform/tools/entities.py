from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class McpTransport(StrEnum):
    STREAMABLE_HTTP = "streamable_http"
    STDIO = "stdio"


class ToolRiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


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
    ) -> "Tool":
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
        )

    def set_enabled(self, enabled: bool) -> "Tool":
        return replace(self, enabled=enabled, updated_at=datetime.now(UTC))
