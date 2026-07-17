"""模型用量记录仓储（C16 阶段二，纯观测面）。

写入：每次物理模型调用结束即落一行（不做跨调用内存缓冲，进程崩溃至多丢失正在进行的
那一次调用记录，不丢已落库的）。多副本并发写各自独立行、主键为随机 UUID，不撞唯一键、
不相互阻塞。

查询：严格按 tenant_id 行级隔离 + keyset 分页（按 recorded_at, id 降序），跨租户读不到。

清扫：用量表随调用无界增长，按保留期有界删除（`prune_older_than` + limit），与
`model_gateway_provisioning_commands` 的 `prune_settled` 同模式。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    and_,
    delete,
    or_,
    select,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.model_gateway.usage import (
    ModelCallOutcome,
    ModelUsageCursor,
    ModelUsagePage,
    ModelUsageQuery,
    ModelUsageRecord,
)


class ModelUsageRow(Base):
    __tablename__ = "model_usage_records"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # tenant_id 是安全/隔离边界，FK + CASCADE 强约束；run/employee 是观测归属标签，
    # 刻意不加 FK：账单/用量记录必须比 run/employee 生命周期更长（删 run 不该抹掉计费历史），
    # 跨租户泄露由查询层的 tenant_id 过滤挡住，不依赖归属 FK。
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    employee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    model_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cost_nanousd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_model_usage_records_latency_non_negative"),
        CheckConstraint(
            "cost_nanousd IS NULL OR cost_nanousd >= 0",
            name="ck_model_usage_records_cost_non_negative",
        ),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_model_usage_records_prompt_tokens_non_negative",
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_model_usage_records_completion_tokens_non_negative",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_model_usage_records_total_tokens_non_negative",
        ),
        CheckConstraint(
            "outcome IN ('success', 'error')",
            name="ck_model_usage_records_outcome",
        ),
        # 按 tenant + 时间范围查询是主访问路径（含 keyset 翻页与保留期清扫）。
        Index("ix_model_usage_records_tenant_recorded", "tenant_id", "recorded_at"),
    )


def _to_entity(row: ModelUsageRow) -> ModelUsageRecord:
    recorded_at = row.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return ModelUsageRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        run_id=row.run_id,
        employee_id=row.employee_id,
        model_alias=row.model_alias,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        latency_ms=row.latency_ms,
        outcome=ModelCallOutcome(row.outcome),
        error_type=row.error_type,
        cost_nanousd=row.cost_nanousd,
        cost_source=row.cost_source,
        recorded_at=recorded_at,
    )


def _to_row(record: ModelUsageRecord) -> ModelUsageRow:
    return ModelUsageRow(
        id=record.id,
        tenant_id=record.tenant_id,
        run_id=record.run_id,
        employee_id=record.employee_id,
        model_alias=record.model_alias,
        prompt_tokens=record.prompt_tokens,
        completion_tokens=record.completion_tokens,
        total_tokens=record.total_tokens,
        latency_ms=record.latency_ms,
        outcome=record.outcome.value,
        error_type=record.error_type,
        cost_nanousd=record.cost_nanousd,
        cost_source=record.cost_source,
        recorded_at=record.recorded_at,
    )


class SqlAlchemyModelUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, record: ModelUsageRecord) -> None:
        self._session.add(_to_row(record))

    async def query(self, query: ModelUsageQuery) -> ModelUsagePage:
        limit = max(1, query.limit)
        stmt = select(ModelUsageRow).where(ModelUsageRow.tenant_id == query.tenant_id)
        if query.start is not None:
            stmt = stmt.where(ModelUsageRow.recorded_at >= query.start)
        if query.end is not None:
            stmt = stmt.where(ModelUsageRow.recorded_at < query.end)
        if query.cursor is not None:
            # keyset：(recorded_at, id) 严格小于游标（降序翻页）。用显式布尔避免不同
            # 后端对 row-value 比较的差异。
            stmt = stmt.where(
                or_(
                    ModelUsageRow.recorded_at < query.cursor.recorded_at,
                    and_(
                        ModelUsageRow.recorded_at == query.cursor.recorded_at,
                        ModelUsageRow.id < query.cursor.id,
                    ),
                )
            )
        stmt = stmt.order_by(ModelUsageRow.recorded_at.desc(), ModelUsageRow.id.desc()).limit(
            limit + 1
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        records = tuple(_to_entity(row) for row in page_rows)
        next_cursor = (
            ModelUsageCursor(recorded_at=records[-1].recorded_at, id=records[-1].id)
            if has_more and records
            else None
        )
        return ModelUsagePage(records=records, next_cursor=next_cursor)

    async def prune_older_than(self, cutoff: datetime, *, limit: int) -> int:
        if limit <= 0:
            return 0
        stale = (
            (
                await self._session.execute(
                    select(ModelUsageRow.id).where(ModelUsageRow.recorded_at < cutoff).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not stale:
            return 0
        result = cast(
            CursorResult[tuple[object, ...]],
            await self._session.execute(delete(ModelUsageRow).where(ModelUsageRow.id.in_(stale))),
        )
        return result.rowcount


class SessionModelUsageRecorder:
    """``ModelUsageRecorder`` 端口的落库实现：每条记录用独立 session 写入并提交。

    捕获层（回调）在每次模型调用结束时调用它；它自身不吞异常，是否「失败不拖垮 Run」由
    捕获层负责（捕获层会在失败时降级为可观测信号而不抛出）。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, record: ModelUsageRecord) -> None:
        async with self._session_factory() as session:
            SqlAlchemyModelUsageRepository(session).add(record)
            await session.commit()


class SessionModelUsagePruner:
    """按保留期有界清扫用量记录（由模型网关 Controller 循环调度）。

    与 `model_gateway_provisioning_commands` 的 `prune_settled` 同模式：一次删 `limit` 条，
    循环推进，把无界增长的观测表成本压在有界范围内。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def prune(self, *, now: datetime, retention: timedelta, limit: int) -> int:
        async with self._session_factory() as session:
            deleted = await SqlAlchemyModelUsageRepository(session).prune_older_than(
                now - retention, limit=limit
            )
            await session.commit()
            return deleted
