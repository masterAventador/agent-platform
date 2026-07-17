"""C12 定时任务与执行历史的 SQLAlchemy 仓储。

并发要点：
- `lock_due_task` 用 `FOR UPDATE SKIP LOCKED` 认领到期任务，多副本互斥；
- `scheduled_task_executions` 的 (scheduled_task_id, scheduled_for) 唯一索引是
  「同一触发点绝不产生第二个 Run」的最终防线，锁失效也兜得住；
- 任务与执行都按 revision CAS 更新，读到写之间的窗口不会被并发覆盖。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
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
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.scheduling.entities import (
    ACTIVE_EXECUTION_STATUSES,
    TERMINAL_EXECUTION_STATUSES,
    ConcurrencyPolicy,
    ExecutionStatus,
    MisfirePolicy,
    PauseReason,
    ScheduledTask,
    ScheduledTaskExecution,
    SkipReason,
)
from agent_platform.platform.scheduling.schedule import Schedule, ScheduleKind


class ScheduledTaskRecord(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    employee_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE")
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    schedule_kind: Mapped[str] = mapped_column(String(16))
    cron_expression: Mapped[str | None] = mapped_column(String(200), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64))
    input_data: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean)
    pause_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    misfire_policy: Mapped[str] = mapped_column(String(16))
    concurrency_policy: Mapped[str] = mapped_column(String(16))
    misfire_grace_seconds: Mapped[int] = mapped_column(Integer)
    misfire_backfill_window_seconds: Mapped[int] = mapped_column(Integer)
    max_retries: Mapped[int] = mapped_column(Integer)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # 索引全部显式命名并与迁移 0035 逐条对齐，避免 autogenerate 漂移守卫误判。
        Index("ix_scheduled_tasks_tenant_id", "tenant_id"),
        Index("ix_scheduled_tasks_employee_id", "employee_id"),
        Index("ix_scheduled_tasks_enabled_next_run_at", "enabled", "next_run_at"),
    )


class ScheduledTaskExecutionRecord(Base):
    __tablename__ = "scheduled_task_executions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    scheduled_task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scheduled_tasks.id", ondelete="CASCADE")
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # 同一任务的同一触发点只允许存在一条执行记录：多副本重复触发的最终防线。
        Index(
            "uq_scheduled_task_executions_task_scheduled_for",
            "scheduled_task_id",
            "scheduled_for",
            unique=True,
        ),
        Index("ix_scheduled_task_executions_tenant_id", "tenant_id"),
        Index("ix_scheduled_task_executions_status_updated_at", "status", "updated_at"),
        Index("ix_scheduled_task_executions_status_next_attempt_at", "status", "next_attempt_at"),
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyScheduledTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: ScheduledTask) -> None:
        self._session.add(self._to_record(task))

    async def get(self, *, tenant_id: UUID, task_id: UUID) -> ScheduledTask | None:
        record = (
            await self._session.execute(
                select(ScheduledTaskRecord).where(
                    ScheduledTaskRecord.tenant_id == tenant_id,
                    ScheduledTaskRecord.id == task_id,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID | None = None,
        employee_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ScheduledTask], int]:
        conditions = [ScheduledTaskRecord.tenant_id == tenant_id]
        if created_by is not None:
            conditions.append(ScheduledTaskRecord.created_by == created_by)
        if employee_id is not None:
            conditions.append(ScheduledTaskRecord.employee_id == employee_id)
        total = (
            await self._session.execute(
                select(func.count()).select_from(ScheduledTaskRecord).where(*conditions)
            )
        ).scalar_one()
        result = await self._session.execute(
            select(ScheduledTaskRecord)
            .where(*conditions)
            .order_by(ScheduledTaskRecord.created_at.desc(), ScheduledTaskRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._to_entity(record) for record in result.scalars()], int(total)

    async def list_due_task_ids(self, *, now: datetime, limit: int) -> Sequence[UUID]:
        """到期候选（不加锁）；真正认领在 `lock_due_task` 里带锁重新判定。"""

        result = await self._session.execute(
            select(ScheduledTaskRecord.id)
            .where(
                ScheduledTaskRecord.enabled.is_(True),
                ScheduledTaskRecord.next_run_at.is_not(None),
                ScheduledTaskRecord.next_run_at <= now,
            )
            .order_by(ScheduledTaskRecord.next_run_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def lock_due_task(self, *, task_id: UUID, now: datetime) -> ScheduledTask | None:
        """带行锁认领一个仍然到期的任务；被别的副本持有时立即返回 None。"""

        record = (
            await self._session.execute(
                select(ScheduledTaskRecord)
                .where(
                    ScheduledTaskRecord.id == task_id,
                    ScheduledTaskRecord.enabled.is_(True),
                    ScheduledTaskRecord.next_run_at.is_not(None),
                    ScheduledTaskRecord.next_run_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def lock_task(self, *, task_id: UUID) -> ScheduledTask | None:
        record = (
            await self._session.execute(
                select(ScheduledTaskRecord)
                .where(ScheduledTaskRecord.id == task_id)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def update_with_cas(self, task: ScheduledTask, *, expected_revision: int) -> bool:
        result = await self._session.execute(
            update(ScheduledTaskRecord)
            .where(
                ScheduledTaskRecord.id == task.id,
                ScheduledTaskRecord.tenant_id == task.tenant_id,
                ScheduledTaskRecord.revision == expected_revision,
            )
            .values(
                name=task.name,
                schedule_kind=task.schedule.kind.value,
                cron_expression=task.schedule.cron_expression,
                run_at=task.schedule.run_at,
                timezone=task.schedule.timezone,
                input_data=task.input_data,
                enabled=task.enabled,
                pause_reason=task.pause_reason.value if task.pause_reason else None,
                next_run_at=task.next_run_at,
                last_run_at=task.last_run_at,
                misfire_policy=task.misfire_policy.value,
                concurrency_policy=task.concurrency_policy.value,
                misfire_grace_seconds=task.misfire_grace_seconds,
                misfire_backfill_window_seconds=task.misfire_backfill_window_seconds,
                max_retries=task.max_retries,
                retry_backoff_seconds=task.retry_backoff_seconds,
                revision=task.revision,
                updated_at=task.updated_at,
            )
        )
        await self._session.flush()
        return isinstance(result, CursorResult) and result.rowcount == 1

    async def delete(self, *, tenant_id: UUID, task_id: UUID) -> bool:
        # 执行历史显式删除，不依赖底层引擎是否开启外键级联（SQLite 默认关闭），
        # 保证同一份代码在测试与生产引擎上语义一致。
        await self._session.execute(
            delete(ScheduledTaskExecutionRecord).where(
                ScheduledTaskExecutionRecord.tenant_id == tenant_id,
                ScheduledTaskExecutionRecord.scheduled_task_id == task_id,
            )
        )
        result = await self._session.execute(
            delete(ScheduledTaskRecord).where(
                ScheduledTaskRecord.tenant_id == tenant_id,
                ScheduledTaskRecord.id == task_id,
            )
        )
        await self._session.flush()
        return isinstance(result, CursorResult) and result.rowcount == 1

    @staticmethod
    def _to_record(task: ScheduledTask) -> ScheduledTaskRecord:
        return ScheduledTaskRecord(
            id=task.id,
            tenant_id=task.tenant_id,
            employee_id=task.employee_id,
            created_by=task.created_by,
            name=task.name,
            schedule_kind=task.schedule.kind.value,
            cron_expression=task.schedule.cron_expression,
            run_at=task.schedule.run_at,
            timezone=task.schedule.timezone,
            input_data=task.input_data,
            enabled=task.enabled,
            pause_reason=task.pause_reason.value if task.pause_reason else None,
            next_run_at=task.next_run_at,
            last_run_at=task.last_run_at,
            misfire_policy=task.misfire_policy.value,
            concurrency_policy=task.concurrency_policy.value,
            misfire_grace_seconds=task.misfire_grace_seconds,
            misfire_backfill_window_seconds=task.misfire_backfill_window_seconds,
            max_retries=task.max_retries,
            retry_backoff_seconds=task.retry_backoff_seconds,
            revision=task.revision,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    @staticmethod
    def _to_entity(record: ScheduledTaskRecord) -> ScheduledTask:
        return ScheduledTask(
            id=record.id,
            tenant_id=record.tenant_id,
            employee_id=record.employee_id,
            created_by=record.created_by,
            name=record.name,
            schedule=Schedule.restore(
                kind=ScheduleKind(record.schedule_kind),
                timezone=record.timezone,
                cron_expression=record.cron_expression,
                run_at=_as_utc(record.run_at) if record.run_at else None,
            ),
            input_data=record.input_data,
            enabled=record.enabled,
            pause_reason=PauseReason(record.pause_reason) if record.pause_reason else None,
            next_run_at=_as_utc(record.next_run_at) if record.next_run_at else None,
            last_run_at=_as_utc(record.last_run_at) if record.last_run_at else None,
            misfire_policy=MisfirePolicy(record.misfire_policy),
            concurrency_policy=ConcurrencyPolicy(record.concurrency_policy),
            misfire_grace_seconds=record.misfire_grace_seconds,
            misfire_backfill_window_seconds=record.misfire_backfill_window_seconds,
            max_retries=record.max_retries,
            retry_backoff_seconds=record.retry_backoff_seconds,
            revision=record.revision,
            created_at=_as_utc(record.created_at),
            updated_at=_as_utc(record.updated_at),
        )


class SqlAlchemyScheduledTaskExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, execution: ScheduledTaskExecution) -> None:
        """写入执行记录；同一触发点重复写入会抛 IntegrityError（唯一索引）。"""

        self._session.add(self._to_record(execution))

    async def get(
        self, *, tenant_id: UUID, execution_id: UUID
    ) -> ScheduledTaskExecution | None:
        record = (
            await self._session.execute(
                select(ScheduledTaskExecutionRecord).where(
                    ScheduledTaskExecutionRecord.tenant_id == tenant_id,
                    ScheduledTaskExecutionRecord.id == execution_id,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list_for_task(
        self,
        *,
        tenant_id: UUID,
        scheduled_task_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ScheduledTaskExecution], int]:
        conditions = [
            ScheduledTaskExecutionRecord.tenant_id == tenant_id,
            ScheduledTaskExecutionRecord.scheduled_task_id == scheduled_task_id,
        ]
        total = (
            await self._session.execute(
                select(func.count()).select_from(ScheduledTaskExecutionRecord).where(*conditions)
            )
        ).scalar_one()
        result = await self._session.execute(
            select(ScheduledTaskExecutionRecord)
            .where(*conditions)
            .order_by(
                ScheduledTaskExecutionRecord.scheduled_for.desc(),
                ScheduledTaskExecutionRecord.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return [self._to_entity(record) for record in result.scalars()], int(total)

    async def list_active_for_task(
        self, *, scheduled_task_id: UUID
    ) -> Sequence[ScheduledTaskExecution]:
        """该任务尚未结算的执行；并发策略据此判断「上一轮还在跑」。"""

        result = await self._session.execute(
            select(ScheduledTaskExecutionRecord)
            .where(
                ScheduledTaskExecutionRecord.scheduled_task_id == scheduled_task_id,
                ScheduledTaskExecutionRecord.status.in_(
                    [status.value for status in ACTIVE_EXECUTION_STATUSES]
                ),
            )
            .order_by(ScheduledTaskExecutionRecord.scheduled_for)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def list_dispatched(self, *, limit: int) -> Sequence[ScheduledTaskExecution]:
        result = await self._session.execute(
            select(ScheduledTaskExecutionRecord)
            .where(ScheduledTaskExecutionRecord.status == ExecutionStatus.DISPATCHED.value)
            .order_by(ScheduledTaskExecutionRecord.updated_at)
            .limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def list_pending_dispatch(
        self, *, now: datetime, limit: int
    ) -> Sequence[ScheduledTaskExecution]:
        """等待派发的执行：排队中的，以及退避时间已到的重试。"""

        result = await self._session.execute(
            select(ScheduledTaskExecutionRecord)
            .where(
                or_(
                    ScheduledTaskExecutionRecord.status == ExecutionStatus.DEFERRED.value,
                    (
                        ScheduledTaskExecutionRecord.status
                        == ExecutionStatus.RETRY_WAITING.value
                    )
                    & (ScheduledTaskExecutionRecord.next_attempt_at <= now),
                )
            )
            .order_by(ScheduledTaskExecutionRecord.scheduled_for)
            .limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def update_with_cas(
        self, execution: ScheduledTaskExecution, *, expected_revision: int
    ) -> bool:
        result = await self._session.execute(
            update(ScheduledTaskExecutionRecord)
            .where(
                ScheduledTaskExecutionRecord.id == execution.id,
                ScheduledTaskExecutionRecord.revision == expected_revision,
            )
            .values(
                status=execution.status.value,
                attempts=execution.attempts,
                run_id=execution.run_id,
                skip_reason=execution.skip_reason.value if execution.skip_reason else None,
                error_message=execution.error_message,
                next_attempt_at=execution.next_attempt_at,
                revision=execution.revision,
                updated_at=execution.updated_at,
            )
        )
        await self._session.flush()
        return isinstance(result, CursorResult) and result.rowcount == 1

    async def purge_terminal_before(self, *, cutoff: datetime, limit: int) -> int:
        """清理过期的终态执行历史；活跃执行永远不删，避免丢失结算。"""

        stale = (
            await self._session.execute(
                select(ScheduledTaskExecutionRecord.id)
                .where(
                    ScheduledTaskExecutionRecord.status.in_(
                        [status.value for status in TERMINAL_EXECUTION_STATUSES]
                    ),
                    ScheduledTaskExecutionRecord.updated_at < cutoff,
                )
                .order_by(ScheduledTaskExecutionRecord.updated_at)
                .limit(limit)
            )
        ).scalars()
        stale_ids = list(stale)
        if not stale_ids:
            return 0
        result = await self._session.execute(
            delete(ScheduledTaskExecutionRecord).where(
                ScheduledTaskExecutionRecord.id.in_(stale_ids)
            )
        )
        await self._session.flush()
        return result.rowcount if isinstance(result, CursorResult) else 0

    @staticmethod
    def _to_record(execution: ScheduledTaskExecution) -> ScheduledTaskExecutionRecord:
        return ScheduledTaskExecutionRecord(
            id=execution.id,
            tenant_id=execution.tenant_id,
            scheduled_task_id=execution.scheduled_task_id,
            scheduled_for=execution.scheduled_for,
            status=execution.status.value,
            attempts=execution.attempts,
            revision=execution.revision,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            run_id=execution.run_id,
            skip_reason=execution.skip_reason.value if execution.skip_reason else None,
            error_message=execution.error_message,
            next_attempt_at=execution.next_attempt_at,
        )

    @staticmethod
    def _to_entity(record: ScheduledTaskExecutionRecord) -> ScheduledTaskExecution:
        return ScheduledTaskExecution(
            id=record.id,
            tenant_id=record.tenant_id,
            scheduled_task_id=record.scheduled_task_id,
            scheduled_for=_as_utc(record.scheduled_for),
            status=ExecutionStatus(record.status),
            attempts=record.attempts,
            revision=record.revision,
            created_at=_as_utc(record.created_at),
            updated_at=_as_utc(record.updated_at),
            run_id=record.run_id,
            skip_reason=SkipReason(record.skip_reason) if record.skip_reason else None,
            error_message=record.error_message,
            next_attempt_at=(
                _as_utc(record.next_attempt_at) if record.next_attempt_at else None
            ),
        )
