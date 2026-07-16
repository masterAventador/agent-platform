from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    delete,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.tool_gateway.models import ToolDefinition
from agent_platform.platform.tools.entities import (
    McpConnectionStatus,
    McpServer,
    McpSyncRemovedEntry,
    McpSyncReport,
    McpTransport,
    Tool,
    ToolApprovalPolicy,
    ToolOrigin,
    ToolReference,
    ToolRiskLevel,
    ToolVersion,
)
from agent_platform.platform.tools.errors import RegistryNameAlreadyExists


class McpServerRecord(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    transport: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list[str]] = mapped_column(JSON)
    secret_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    connection_status: Mapped[str] = mapped_column(String(16), default="unknown")
    connection_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    connection_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("uq_mcp_servers_tenant_name", tenant_id, name, unique=True),)


class ToolRecord(Base):
    __tablename__ = "tools"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(2000))
    input_schema: Mapped[dict[str, object]] = mapped_column(JSON)
    risk_level: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(16), default="manual")
    approval_policy: Mapped[str] = mapped_column(String(16), default="risk_based")
    upstream_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("uq_tools_server_name", server_id, name, unique=True),)


class ToolVersionRecord(Base):
    __tablename__ = "tool_versions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    tool_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(2000))
    input_schema: Mapped[dict[str, object]] = mapped_column(JSON)
    risk_level: Mapped[str] = mapped_column(String(16))
    approval_policy: Mapped[str] = mapped_column(String(16))
    change_source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_tool_versions_number", tool_id, version, unique=True),)


class McpSyncReportRecord(Base):
    __tablename__ = "mcp_sync_reports"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    added: Mapped[list[str]] = mapped_column(JSON)
    updated: Mapped[list[str]] = mapped_column(JSON)
    removed: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SqlAlchemyToolRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_server(self, server: McpServer) -> None:
        self._session.add(self._server_record(server))
        await self._flush_unique()

    async def get_server(self, *, tenant_id: UUID, server_id: UUID) -> McpServer | None:
        result = await self._session.execute(
            select(McpServerRecord).where(
                McpServerRecord.tenant_id == tenant_id,
                McpServerRecord.id == server_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._server_entity(record) if record is not None else None

    async def list_servers(self, *, tenant_id: UUID) -> list[McpServer]:
        result = await self._session.execute(
            select(McpServerRecord)
            .where(McpServerRecord.tenant_id == tenant_id)
            .order_by(McpServerRecord.created_at)
        )
        return [self._server_entity(record) for record in result.scalars()]

    async def get_server_for_update(
        self, *, tenant_id: UUID, server_id: UUID
    ) -> McpServer | None:
        result = await self._session.execute(
            select(McpServerRecord)
            .where(
                McpServerRecord.tenant_id == tenant_id,
                McpServerRecord.id == server_id,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        return self._server_entity(record) if record is not None else None

    async def update_server(self, server: McpServer) -> None:
        record = await self._session.get(McpServerRecord, server.id)
        if record is not None and record.tenant_id == server.tenant_id:
            record.name = server.name
            record.endpoint = server.endpoint
            record.command = server.command
            record.args = server.args
            record.secret_reference = server.secret_reference
            record.enabled = server.enabled
            record.updated_at = server.updated_at
            record.connection_status = server.connection_status.value
            record.connection_tested_at = server.connection_tested_at
            record.connection_error_code = server.connection_error_code
            record.last_synced_at = server.last_synced_at
            await self._flush_unique()

    async def delete_server(self, *, tenant_id: UUID, server_id: UUID) -> None:
        record = await self._session.get(McpServerRecord, server_id)
        if record is None or record.tenant_id != tenant_id:
            return
        tool_ids = select(ToolRecord.id).where(
            ToolRecord.tenant_id == tenant_id, ToolRecord.server_id == server_id
        )
        await self._session.execute(
            delete(ToolVersionRecord).where(ToolVersionRecord.tool_id.in_(tool_ids))
        )
        await self._session.execute(
            delete(ToolRecord).where(
                ToolRecord.tenant_id == tenant_id, ToolRecord.server_id == server_id
            )
        )
        await self._session.execute(
            delete(McpSyncReportRecord).where(
                McpSyncReportRecord.tenant_id == tenant_id,
                McpSyncReportRecord.server_id == server_id,
            )
        )
        await self._session.delete(record)
        await self._session.flush()

    async def add_tool(self, tool: Tool) -> None:
        self._session.add(self._tool_record(tool))
        await self._flush_unique()

    async def get_tool(self, *, tenant_id: UUID, tool_id: UUID) -> Tool | None:
        result = await self._session.execute(
            select(ToolRecord).where(
                ToolRecord.tenant_id == tenant_id,
                ToolRecord.id == tool_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._tool_entity(record) if record is not None else None

    async def list_tools(
        self, *, tenant_id: UUID, server_id: UUID | None = None
    ) -> list[Tool]:
        statement = select(ToolRecord).where(ToolRecord.tenant_id == tenant_id)
        if server_id is not None:
            statement = statement.where(ToolRecord.server_id == server_id)
        result = await self._session.execute(statement.order_by(ToolRecord.created_at))
        return [self._tool_entity(record) for record in result.scalars()]

    async def are_bindable(self, *, tenant_id: UUID, tool_ids: list[UUID]) -> bool:
        unique_ids = set(tool_ids)
        if not unique_ids:
            return True
        result = await self._session.execute(
            select(func.count())
            .select_from(ToolRecord)
            .join(McpServerRecord, McpServerRecord.id == ToolRecord.server_id)
            .where(
                ToolRecord.tenant_id == tenant_id,
                McpServerRecord.tenant_id == tenant_id,
                ToolRecord.id.in_(unique_ids),
                ToolRecord.enabled.is_(True),
                McpServerRecord.enabled.is_(True),
            )
        )
        return result.scalar_one() == len(unique_ids)

    async def resolve(
        self,
        *,
        tenant_id: UUID,
        tool_id: UUID,
    ) -> ToolDefinition | None:
        result = await self._session.execute(
            select(ToolRecord, McpServerRecord)
            .join(McpServerRecord, McpServerRecord.id == ToolRecord.server_id)
            .where(
                ToolRecord.tenant_id == tenant_id,
                McpServerRecord.tenant_id == tenant_id,
                ToolRecord.id == tool_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        tool, server = row
        references = (server.secret_reference,) if server.secret_reference is not None else ()
        return ToolDefinition(
            tenant_id=tenant_id,
            tool_id=tool.id,
            server_id=server.id,
            name=tool.name,
            risk=ToolRiskLevel(tool.risk_level),
            enabled=tool.enabled,
            server_enabled=server.enabled,
            credential_references=references,
            approval_policy=ToolApprovalPolicy(tool.approval_policy),
            upstream_missing=tool.upstream_missing,
        )

    async def update_tool(self, tool: Tool) -> None:
        record = await self._session.get(ToolRecord, tool.id)
        if record is not None and record.tenant_id == tool.tenant_id:
            record.description = tool.description
            record.input_schema = tool.input_schema
            record.risk_level = tool.risk_level.value
            record.approval_policy = tool.approval_policy.value
            record.enabled = tool.enabled
            record.upstream_missing = tool.upstream_missing
            record.version = tool.version
            record.updated_at = tool.updated_at
            await self._session.flush()

    async def delete_tool(self, *, tenant_id: UUID, tool_id: UUID) -> None:
        record = await self._session.get(ToolRecord, tool_id)
        if record is None or record.tenant_id != tenant_id:
            return
        await self._session.execute(
            delete(ToolVersionRecord).where(
                ToolVersionRecord.tenant_id == tenant_id,
                ToolVersionRecord.tool_id == tool_id,
            )
        )
        await self._session.delete(record)
        await self._session.flush()

    async def add_tool_version(self, version: ToolVersion) -> None:
        self._session.add(
            ToolVersionRecord(
                id=version.id,
                tenant_id=version.tenant_id,
                tool_id=version.tool_id,
                version=version.version,
                description=version.description,
                input_schema=version.input_schema,
                risk_level=version.risk_level.value,
                approval_policy=version.approval_policy.value,
                change_source=version.change_source,
                created_at=version.created_at,
            )
        )
        await self._session.flush()

    async def list_tool_versions(
        self, *, tenant_id: UUID, tool_id: UUID
    ) -> list[ToolVersion]:
        result = await self._session.execute(
            select(ToolVersionRecord)
            .where(
                ToolVersionRecord.tenant_id == tenant_id,
                ToolVersionRecord.tool_id == tool_id,
            )
            .order_by(ToolVersionRecord.version)
        )
        return [self._version_entity(record) for record in result.scalars()]

    async def get_tool_version(
        self, *, tenant_id: UUID, tool_id: UUID, version: int
    ) -> ToolVersion | None:
        result = await self._session.execute(
            select(ToolVersionRecord).where(
                ToolVersionRecord.tenant_id == tenant_id,
                ToolVersionRecord.tool_id == tool_id,
                ToolVersionRecord.version == version,
            )
        )
        record = result.scalar_one_or_none()
        return self._version_entity(record) if record is not None else None

    async def list_tool_references(
        self, *, tenant_id: UUID, tool_ids: Sequence[UUID]
    ) -> list[ToolReference]:
        from agent_platform.infrastructure.database.repositories.employees import (
            EmployeeRecord,
            EmployeeVersionRecord,
        )

        wanted = {str(tool_id): tool_id for tool_id in tool_ids}
        if not wanted:
            return []
        references: list[ToolReference] = []
        drafts = await self._session.execute(
            select(EmployeeRecord).where(EmployeeRecord.tenant_id == tenant_id)
        )
        for employee in drafts.scalars():
            for tool_id_text in employee.tool_ids:
                if tool_id_text in wanted:
                    references.append(
                        ToolReference(
                            tool_id=wanted[tool_id_text],
                            employee_id=employee.id,
                            employee_name=employee.name,
                            relation="employee_draft",
                        )
                    )
        versions = await self._session.execute(
            select(EmployeeVersionRecord).where(
                EmployeeVersionRecord.tenant_id == tenant_id
            )
        )
        for version in versions.scalars():
            definition_tool_ids = version.definition.get("tool_ids")
            if not isinstance(definition_tool_ids, list):
                continue
            for tool_id_text in definition_tool_ids:
                if isinstance(tool_id_text, str) and tool_id_text in wanted:
                    references.append(
                        ToolReference(
                            tool_id=wanted[tool_id_text],
                            employee_id=version.employee_id,
                            employee_name=str(version.definition.get("name") or "未命名员工"),
                            relation="employee_version",
                            version=version.version,
                        )
                    )
        return references

    async def add_sync_report(self, report: McpSyncReport, *, keep: int) -> None:
        self._session.add(
            McpSyncReportRecord(
                id=report.id,
                tenant_id=report.tenant_id,
                server_id=report.server_id,
                occurred_at=report.occurred_at,
                status=report.status,
                added=list(report.added),
                updated=list(report.updated),
                removed=[
                    {"name": entry.name, "referenced": entry.referenced}
                    for entry in report.removed
                ],
                unchanged=report.unchanged,
                error_code=report.error_code,
            )
        )
        await self._session.flush()
        stale = await self._session.execute(
            select(McpSyncReportRecord.id)
            .where(
                McpSyncReportRecord.tenant_id == report.tenant_id,
                McpSyncReportRecord.server_id == report.server_id,
            )
            .order_by(
                McpSyncReportRecord.occurred_at.desc(), McpSyncReportRecord.id.desc()
            )
            .offset(keep)
        )
        stale_ids = [row[0] for row in stale]
        if stale_ids:
            await self._session.execute(
                delete(McpSyncReportRecord).where(McpSyncReportRecord.id.in_(stale_ids))
            )
            await self._session.flush()

    async def list_sync_reports(
        self, *, tenant_id: UUID, server_id: UUID, limit: int
    ) -> list[McpSyncReport]:
        result = await self._session.execute(
            select(McpSyncReportRecord)
            .where(
                McpSyncReportRecord.tenant_id == tenant_id,
                McpSyncReportRecord.server_id == server_id,
            )
            .order_by(
                McpSyncReportRecord.occurred_at.desc(), McpSyncReportRecord.id.desc()
            )
            .limit(limit)
        )
        return [self._report_entity(record) for record in result.scalars()]

    async def _flush_unique(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise RegistryNameAlreadyExists from error

    @staticmethod
    def _server_record(server: McpServer) -> McpServerRecord:
        return McpServerRecord(
            id=server.id,
            tenant_id=server.tenant_id,
            name=server.name,
            transport=server.transport.value,
            endpoint=server.endpoint,
            command=server.command,
            args=server.args,
            secret_reference=server.secret_reference,
            enabled=server.enabled,
            created_by=server.created_by,
            created_at=server.created_at,
            updated_at=server.updated_at,
            connection_status=server.connection_status.value,
            connection_tested_at=server.connection_tested_at,
            connection_error_code=server.connection_error_code,
            last_synced_at=server.last_synced_at,
        )

    @staticmethod
    def _tool_record(tool: Tool) -> ToolRecord:
        return ToolRecord(
            id=tool.id,
            tenant_id=tool.tenant_id,
            server_id=tool.server_id,
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            risk_level=tool.risk_level.value,
            enabled=tool.enabled,
            created_at=tool.created_at,
            updated_at=tool.updated_at,
            origin=tool.origin.value,
            approval_policy=tool.approval_policy.value,
            upstream_missing=tool.upstream_missing,
            version=tool.version,
        )

    @classmethod
    def _server_entity(cls, record: McpServerRecord) -> McpServer:
        return McpServer(
            id=record.id,
            tenant_id=record.tenant_id,
            name=record.name,
            transport=McpTransport(record.transport),
            endpoint=record.endpoint,
            command=record.command,
            args=record.args,
            secret_reference=record.secret_reference,
            enabled=record.enabled,
            created_by=record.created_by,
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
            connection_status=McpConnectionStatus(record.connection_status),
            connection_tested_at=(
                cls._as_utc(record.connection_tested_at)
                if record.connection_tested_at is not None
                else None
            ),
            connection_error_code=record.connection_error_code,
            last_synced_at=(
                cls._as_utc(record.last_synced_at)
                if record.last_synced_at is not None
                else None
            ),
        )

    @classmethod
    def _tool_entity(cls, record: ToolRecord) -> Tool:
        return Tool(
            id=record.id,
            tenant_id=record.tenant_id,
            server_id=record.server_id,
            name=record.name,
            description=record.description,
            input_schema=record.input_schema,
            risk_level=ToolRiskLevel(record.risk_level),
            enabled=record.enabled,
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
            origin=ToolOrigin(record.origin),
            approval_policy=ToolApprovalPolicy(record.approval_policy),
            upstream_missing=record.upstream_missing,
            version=record.version,
        )

    @classmethod
    def _version_entity(cls, record: ToolVersionRecord) -> ToolVersion:
        return ToolVersion(
            id=record.id,
            tenant_id=record.tenant_id,
            tool_id=record.tool_id,
            version=record.version,
            description=record.description,
            input_schema=record.input_schema,
            risk_level=ToolRiskLevel(record.risk_level),
            approval_policy=ToolApprovalPolicy(record.approval_policy),
            change_source=record.change_source,
            created_at=cls._as_utc(record.created_at),
        )

    @classmethod
    def _report_entity(cls, record: McpSyncReportRecord) -> McpSyncReport:
        return McpSyncReport(
            id=record.id,
            tenant_id=record.tenant_id,
            server_id=record.server_id,
            occurred_at=cls._as_utc(record.occurred_at),
            status=record.status,
            added=list(record.added),
            updated=list(record.updated),
            removed=[
                McpSyncRemovedEntry(
                    name=str(entry.get("name", "")),
                    referenced=bool(entry.get("referenced", False)),
                )
                for entry in record.removed
                if isinstance(entry, dict)
            ],
            unchanged=record.unchanged,
            error_code=record.error_code,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
