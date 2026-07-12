from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Uuid, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.tool_gateway.models import ToolDefinition
from agent_platform.platform.tools.entities import (
    McpServer,
    McpTransport,
    Tool,
    ToolRiskLevel,
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

    __table_args__ = (Index("uq_tools_server_name", server_id, name, unique=True),)


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

    async def update_server(self, server: McpServer) -> None:
        record = await self._session.get(McpServerRecord, server.id)
        if record is not None and record.tenant_id == server.tenant_id:
            record.enabled = server.enabled
            record.updated_at = server.updated_at
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
        )

    async def update_tool(self, tool: Tool) -> None:
        record = await self._session.get(ToolRecord, tool.id)
        if record is not None and record.tenant_id == tool.tenant_id:
            record.enabled = tool.enabled
            record.updated_at = tool.updated_at
            await self._session.flush()

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
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
