from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Uuid
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.tool_gateway.models import ToolAuditEvent


class ToolAuditPersistenceError(RuntimeError):
    """Sanitized error raised when an audit event cannot be persisted."""


class ToolAuditRecord(Base):
    __tablename__ = "tool_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    employee_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tool_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tool_name: Mapped[str] = mapped_column(String(128))
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    argument_keys: Mapped[list[str]] = mapped_column(JSON)
    argument_sha256: Mapped[str] = mapped_column(String(64))
    argument_size_bytes: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SqlAlchemyToolAuditSink:
    """Persist every audit event through its own transaction boundary."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def emit(self, event: ToolAuditEvent) -> None:
        try:
            async with self._session_factory() as session:
                session.add(self._record(event))
                await session.commit()
        except Exception:
            raise ToolAuditPersistenceError("Tool audit persistence failed") from None

    @staticmethod
    def _record(event: ToolAuditEvent) -> ToolAuditRecord:
        return ToolAuditRecord(
            id=uuid4(),
            event_type=event.event_type.value,
            occurred_at=event.occurred_at,
            tenant_id=event.tenant_id,
            run_id=event.run_id,
            employee_id=event.employee_id,
            user_id=event.user_id,
            tool_id=event.tool_id,
            tool_name=event.tool_name,
            risk=event.risk.value if event.risk is not None else None,
            argument_keys=list(event.argument_summary.keys),
            argument_sha256=event.argument_summary.sha256,
            argument_size_bytes=event.argument_summary.size_bytes,
            reason=event.reason,
            succeeded=event.succeeded,
        )
