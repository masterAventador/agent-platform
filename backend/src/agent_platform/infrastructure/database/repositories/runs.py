from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
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
    thread_id: Mapped[str] = mapped_column(String(200), unique=True)
    input_data: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(4000), nullable=True)


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
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, run_id: UUID) -> Run | None:
        result = await self._session.execute(
            select(RunRecord).where(
                RunRecord.id == run_id,
                RunRecord.tenant_id == tenant_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

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
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyRunEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: PlatformEvent) -> None:
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
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
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
