from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint, Uuid, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base


class RunDeadLetterRecord(Base):
    __tablename__ = "run_dead_letters"
    __table_args__ = (UniqueConstraint("source_stream", "original_delivery_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_stream: Mapped[str] = mapped_column(String(200))
    original_delivery_id: Mapped[str] = mapped_column(String(100))
    original_command_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    original_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer)
    error_type: Mapped[str] = mapped_column(String(64))
    is_malformed: Mapped[bool] = mapped_column(Boolean)
    raw_fields_summary: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    replayed_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    replayed_command_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    mirrored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class SqlAlchemyRunDeadLetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: RunDeadLetterRecord) -> None:
        self._session.add(record)
        await self._session.flush()

    async def get(
        self,
        dead_letter_id: UUID,
        *,
        tenant_id: UUID,
        for_update: bool = False,
    ) -> RunDeadLetterRecord | None:
        query = select(RunDeadLetterRecord).where(
            RunDeadLetterRecord.id == dead_letter_id,
            RunDeadLetterRecord.tenant_id == tenant_id,
        )
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_delivery_id(
        self,
        *,
        source_stream: str,
        delivery_id: str,
    ) -> RunDeadLetterRecord | None:
        result = await self._session.execute(
            select(RunDeadLetterRecord).where(
                RunDeadLetterRecord.source_stream == source_stream,
                RunDeadLetterRecord.original_delivery_id == delivery_id,
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        tenant_id: UUID,
        limit: int = 100,
        only_unmirrored: bool = False,
    ) -> list[RunDeadLetterRecord]:
        query = select(RunDeadLetterRecord).where(
            RunDeadLetterRecord.tenant_id == tenant_id,
        )
        if only_unmirrored:
            query = query.where(RunDeadLetterRecord.mirrored_at.is_(None))
        result = await self._session.execute(
            query.order_by(
                RunDeadLetterRecord.failed_at.desc(),
                RunDeadLetterRecord.id.desc(),
            ).limit(limit)
        )
        return list(result.scalars())

    async def list_unmirrored_ids_for_worker(self, *, limit: int) -> Sequence[UUID]:
        result = await self._session.execute(
            select(RunDeadLetterRecord.id)
            .where(RunDeadLetterRecord.mirrored_at.is_(None))
            .order_by(RunDeadLetterRecord.failed_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def get_unmirrored_for_worker(
        self,
        dead_letter_id: UUID,
    ) -> RunDeadLetterRecord | None:
        result = await self._session.execute(
            select(RunDeadLetterRecord)
            .where(
                RunDeadLetterRecord.id == dead_letter_id,
                RunDeadLetterRecord.mirrored_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def mark_replayed(
        record: RunDeadLetterRecord,
        *,
        run_id: UUID,
        command_id: UUID,
    ) -> None:
        record.replayed_run_id = run_id
        record.replayed_command_id = command_id
        record.replayed_at = datetime.now(UTC)

    @staticmethod
    def mark_mirrored(record: RunDeadLetterRecord) -> None:
        record.mirrored_at = datetime.now(UTC)
