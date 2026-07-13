from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Uuid, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.tool_gateway.errors import ToolInvocationClaimRejected
from agent_platform.platform.tool_gateway.models import AuditEventType, ToolAuditEvent


class ToolAuditPersistenceError(RuntimeError):
    """Sanitized error raised when an audit event cannot be persisted."""


class ToolAuditRecord(Base):
    __tablename__ = "tool_audit_events"
    __table_args__ = (
        Index(
            "ix_tool_audit_events_invocation_id",
            "invocation_id",
            postgresql_where=text("invocation_id IS NOT NULL"),
            sqlite_where=text("invocation_id IS NOT NULL"),
        ),
    )

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
    invocation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )


class SqlAlchemyToolAuditSink:
    """Persist every audit event through its own transaction boundary."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def emit(self, event: ToolAuditEvent) -> None:
        try:
            async with self._session_factory() as session:
                if event.event_type is AuditEventType.STARTED:
                    await self._assert_started_claim_allowed(session, event)
                session.add(self._record(event))
                await session.commit()
        except ToolInvocationClaimRejected:
            raise
        except Exception:
            raise ToolAuditPersistenceError("Tool audit persistence failed") from None

    async def _assert_started_claim_allowed(
        self,
        session: AsyncSession,
        event: ToolAuditEvent,
    ) -> None:
        run = await SqlAlchemyRunRepository(session).get_for_update(
            tenant_id=event.tenant_id,
            run_id=event.run_id,
        )
        pending_cancel = await SqlAlchemyRunCommandRepository(session).unprocessed_cancel_commands(
            run_id=event.run_id
        )
        if (
            run is None
            or run.status
            in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }
            or pending_cancel
        ):
            raise ToolInvocationClaimRejected
        await self._after_started_claim_locked()

    async def _after_started_claim_locked(self) -> None:
        """Extension seam used by deterministic lock-order integration tests."""

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
            invocation_id=event.invocation_id,
        )


class SqlAlchemyToolAuditReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_started(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        invocation_id: UUID,
    ) -> bool:
        result = await self._session.execute(
            select(ToolAuditRecord.id)
            .where(
                ToolAuditRecord.invocation_id == invocation_id,
                ToolAuditRecord.tenant_id == tenant_id,
                ToolAuditRecord.run_id == run_id,
                ToolAuditRecord.event_type == AuditEventType.STARTED.value,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
