from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import Select

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent


class EventSequenceConflict(Exception):
    """同一任务的事件序号已存在。"""


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    employee_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employees.id"),
        index=True,
    )
    employee_version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    thread_id: Mapped[str] = mapped_column(String(200))
    input_data: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    idempotency_key: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id"), nullable=True, index=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_runs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "created_by",
            "employee_id",
            "idempotency_key",
            name="uq_runs_creation_idempotency",
        ),
    )


class RunEventRecord(Base):
    __tablename__ = "run_events"

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_version: Mapped[str] = mapped_column(String(16))
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    employee_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employees.id"),
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("run_id", "sequence"),)


class RunCommandRecord(Base):
    __tablename__ = "run_commands"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class SqlAlchemyRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: Run) -> None:
        self._session.add(
            RunRecord(
                id=run.id,
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                employee_version=run.employee_version,
                created_by=run.created_by,
                thread_id=run.thread_id,
                input_data=run.input_data,
                status=run.status.value,
                created_at=run.created_at,
                updated_at=run.updated_at,
                started_at=run.started_at,
                finished_at=run.finished_at,
                error_code=run.error_code,
                error_message=run.error_message,
                idempotency_key=run.idempotency_key,
                conversation_id=run.conversation_id,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, run_id: UUID) -> Run | None:
        return await self._get(tenant_id=tenant_id, run_id=run_id, for_update=False)

    async def get_for_update(self, *, tenant_id: UUID, run_id: UUID) -> Run | None:
        return await self._get(tenant_id=tenant_id, run_id=run_id, for_update=True)

    async def get_by_idempotency_key(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        employee_id: UUID,
        idempotency_key: UUID,
    ) -> Run | None:
        record = (
            await self._session.execute(
                select(RunRecord).where(
                    RunRecord.tenant_id == tenant_id,
                    RunRecord.created_by == created_by,
                    RunRecord.employee_id == employee_id,
                    RunRecord.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def _get(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        for_update: bool,
    ) -> Run | None:
        query = select(RunRecord).where(
            RunRecord.id == run_id,
            RunRecord.tenant_id == tenant_id,
        )
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def update(self, run: Run) -> None:
        result = await self._session.execute(
            select(RunRecord).where(RunRecord.id == run.id, RunRecord.tenant_id == run.tenant_id)
        )
        record = result.scalar_one()
        record.status = run.status.value
        record.updated_at = run.updated_at
        record.started_at = run.started_at
        record.finished_at = run.finished_at
        record.error_code = run.error_code
        record.error_message = run.error_message
        await self._session.flush()

    async def list(
        self,
        *,
        tenant_id: UUID,
        limit: int = 100,
        created_by: UUID | None = None,
    ) -> list[Run]:
        query = select(RunRecord).where(RunRecord.tenant_id == tenant_id)
        if created_by is not None:
            query = query.where(RunRecord.created_by == created_by)
        result = await self._session.execute(
            query.order_by(RunRecord.created_at.desc()).limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def list_recovery_candidates(
        self,
        *,
        limit: int = 100,
        after_updated_at: datetime | None = None,
        after_run_id: UUID | None = None,
    ) -> Sequence[Run]:
        query = select(RunRecord).where(
            RunRecord.status.in_(
                [
                    RunStatus.RUNNING.value,
                    RunStatus.WAITING_FOR_INPUT.value,
                    RunStatus.WAITING_FOR_APPROVAL.value,
                ]
            )
        )
        if after_updated_at is not None and after_run_id is not None:
            query = query.where(
                or_(
                    RunRecord.updated_at > after_updated_at,
                    and_(
                        RunRecord.updated_at == after_updated_at,
                        RunRecord.id > after_run_id,
                    ),
                )
            )
        result = await self._session.execute(
            query.order_by(RunRecord.updated_at, RunRecord.id).limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    @classmethod
    def _to_entity(cls, record: RunRecord) -> Run:
        return Run(
            id=record.id,
            tenant_id=record.tenant_id,
            employee_id=record.employee_id,
            employee_version=record.employee_version,
            created_by=record.created_by,
            thread_id=record.thread_id,
            input_data=record.input_data,
            status=RunStatus(record.status),
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
            started_at=cls._as_utc(record.started_at) if record.started_at else None,
            finished_at=cls._as_utc(record.finished_at) if record.finished_at else None,
            error_code=record.error_code,
            error_message=record.error_message,
            idempotency_key=record.idempotency_key,
            conversation_id=record.conversation_id,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyRunEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: PlatformEvent) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add(
                    RunEventRecord(
                        event_id=event.event_id,
                        event_version=event.event_version,
                        tenant_id=event.tenant_id,
                        employee_id=event.employee_id,
                        run_id=event.run_id,
                        sequence=event.sequence,
                        event_type=event.type.value,
                        occurred_at=event.occurred_at,
                        payload=event.payload,
                    )
                )
                await self._session.flush()
        except IntegrityError as error:
            raise EventSequenceConflict from error

    async def list(self, *, run_id: UUID, after_sequence: int) -> list[PlatformEvent]:
        result = await self._session.execute(
            select(RunEventRecord)
            .where(
                RunEventRecord.run_id == run_id,
                RunEventRecord.sequence > after_sequence,
            )
            .order_by(RunEventRecord.sequence)
        )
        return [
            PlatformEvent(
                event_id=record.event_id,
                event_version="1.0",
                tenant_id=record.tenant_id,
                employee_id=record.employee_id,
                run_id=record.run_id,
                sequence=record.sequence,
                type=EventType(record.event_type),
                occurred_at=SqlAlchemyRunRepository._as_utc(record.occurred_at),
                payload=record.payload,
            )
            for record in result.scalars()
        ]

    async def next_sequence(self, *, run_id: UUID) -> int:
        locked_run = await self._session.execute(
            select(RunRecord.id).where(RunRecord.id == run_id).with_for_update()
        )
        if locked_run.scalar_one_or_none() is None:
            raise LookupError("run not found while allocating event sequence")
        result = await self._session.execute(
            select(func.coalesce(func.max(RunEventRecord.sequence), 0)).where(
                RunEventRecord.run_id == run_id
            )
        )
        return int(result.scalar_one()) + 1


class SqlAlchemyRunCommandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, command: RunCommand) -> None:
        self._session.add(
            RunCommandRecord(
                id=command.id,
                run_id=command.run_id,
                tenant_id=command.tenant_id,
                action=command.action.value,
                payload=command.payload,
                created_at=command.created_at,
                dispatched_at=command.dispatched_at,
                processed_at=command.processed_at,
                attempts=command.attempts,
                last_error=command.last_error,
            )
        )
        await self._session.flush()

    async def get(self, command_id: UUID) -> RunCommand | None:
        record = await self._session.get(RunCommandRecord, command_id)
        return self._to_entity(record) if record is not None else None

    async def pending(self, *, limit: int = 100) -> list[RunCommand]:
        result = await self._session.execute(self.pending_query(limit=limit))
        return [self._to_entity(record) for record in result.scalars()]

    @staticmethod
    def pending_query(*, limit: int) -> Select[tuple[RunCommandRecord]]:
        return (
            select(RunCommandRecord)
            .where(
                RunCommandRecord.dispatched_at.is_(None),
                RunCommandRecord.processed_at.is_(None),
            )
            .order_by(RunCommandRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    async def mark_dispatched(self, command_id: UUID) -> None:
        record = await self._session.get(RunCommandRecord, command_id)
        if record is None:
            raise LookupError(command_id)
        record.dispatched_at = datetime.now(UTC)
        record.attempts += 1
        record.last_error = None
        await self._session.flush()

    async def mark_failed(self, command_id: UUID, error: str) -> None:
        record = await self._session.get(RunCommandRecord, command_id)
        if record is None:
            raise LookupError(command_id)
        record.attempts += 1
        record.last_error = error[:2000]
        await self._session.flush()

    async def is_processed(self, command_id: UUID) -> bool:
        record = await self._session.get(RunCommandRecord, command_id)
        return record is not None and record.processed_at is not None

    async def mark_processed(self, command_id: UUID) -> None:
        record = await self._session.get(RunCommandRecord, command_id)
        if record is None:
            raise LookupError(command_id)
        record.processed_at = datetime.now(UTC)
        await self._session.flush()

    async def unprocessed_approval_commands(self, *, run_id: UUID) -> list[RunCommand]:
        result = await self._session.execute(
            select(RunCommandRecord)
            .where(
                RunCommandRecord.run_id == run_id,
                RunCommandRecord.processed_at.is_(None),
                RunCommandRecord.action.in_(
                    [RunCommandAction.APPROVE.value, RunCommandAction.REJECT.value]
                ),
            )
            .with_for_update()
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def unprocessed_cancel_commands(self, *, run_id: UUID) -> list[RunCommand]:
        result = await self._session.execute(
            select(RunCommandRecord)
            .where(
                RunCommandRecord.run_id == run_id,
                RunCommandRecord.processed_at.is_(None),
                RunCommandRecord.action == RunCommandAction.CANCEL.value,
            )
            .order_by(RunCommandRecord.created_at)
        )
        return [self._to_entity(record) for record in result.scalars()]

    @staticmethod
    def _to_entity(record: RunCommandRecord) -> RunCommand:
        return RunCommand(
            id=record.id,
            run_id=record.run_id,
            tenant_id=record.tenant_id,
            action=RunCommandAction(record.action),
            payload=record.payload,
            created_at=SqlAlchemyRunRepository._as_utc(record.created_at),
            dispatched_at=(
                SqlAlchemyRunRepository._as_utc(record.dispatched_at)
                if record.dispatched_at
                else None
            ),
            processed_at=(
                SqlAlchemyRunRepository._as_utc(record.processed_at)
                if record.processed_at
                else None
            ),
            attempts=record.attempts,
            last_error=record.last_error,
        )
