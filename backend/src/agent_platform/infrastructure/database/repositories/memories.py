"""长期记忆 SQLAlchemy 仓储。

``memories`` 保存跨任务长期知识（平台业务数据），与 LangGraph
Checkpoint（运行内执行状态）职责分离；运行时召回只经过本仓储的
``search_for_runtime``，按命名空间权限过滤，读取时判定过期。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CursorResult,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Uuid,
    delete,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.conversations import (
    ConversationRecord,
)
from agent_platform.platform.memory.entities import (
    MAX_MEMORY_CONTENT_CHARS,
    MEMORY_RUNTIME_INJECTION_LIMIT,
    Memory,
    MemoryScope,
    MemorySource,
    MemoryStatus,
)


class MemoryRecord(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(32))
    scope_ref: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    key: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(String(MAX_MEMORY_CONTENT_CHARS))
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        Index(
            "uq_memories_namespace_source_key",
            "tenant_id",
            "scope",
            "scope_ref",
            "source",
            "key",
            unique=True,
        ),
        Index("ix_memories_namespace", "tenant_id", "scope", "scope_ref"),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyMemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, memory: Memory) -> None:
        self._session.add(self._to_record(memory))
        await self._session.flush()

    async def upsert(self, memory: Memory) -> Memory:
        """同 (tenant, scope, scope_ref, source, key) 收编为一行。

        Worker 重投递与并发写同键都会命中唯一约束，此时按业务唯一键
        查回已有行并 touch（内容由 key 派生，语义一致），不重复落行。
        """

        try:
            async with self._session.begin_nested():
                self._session.add(self._to_record(memory))
                await self._session.flush()
            return memory
        except IntegrityError:
            existing = (
                await self._session.execute(
                    select(MemoryRecord).where(
                        MemoryRecord.tenant_id == memory.tenant_id,
                        MemoryRecord.scope == memory.scope.value,
                        MemoryRecord.scope_ref == memory.scope_ref,
                        MemoryRecord.source == memory.source.value,
                        MemoryRecord.key == memory.key,
                    )
                )
            ).scalar_one()
            existing.content = memory.content
            existing.confidence = memory.confidence
            existing.updated_at = datetime.now(UTC)
            await self._session.flush()
            return self._to_entity(existing)

    async def get(self, *, tenant_id: UUID, memory_id: UUID) -> Memory | None:
        record = (
            await self._session.execute(
                select(MemoryRecord).where(
                    MemoryRecord.tenant_id == tenant_id,
                    MemoryRecord.id == memory_id,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def update(self, memory: Memory) -> None:
        record = (
            await self._session.execute(
                select(MemoryRecord).where(
                    MemoryRecord.tenant_id == memory.tenant_id,
                    MemoryRecord.id == memory.id,
                )
            )
        ).scalar_one()
        record.content = memory.content
        record.confidence = memory.confidence
        record.status = memory.status.value
        record.expires_at = memory.expires_at
        record.updated_at = memory.updated_at
        await self._session.flush()

    async def delete(self, *, tenant_id: UUID, memory_id: UUID) -> bool:
        result = await self._session.execute(
            delete(MemoryRecord).where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.id == memory_id,
            )
        )
        return isinstance(result, CursorResult) and bool(result.rowcount)

    async def list(
        self,
        *,
        tenant_id: UUID,
        visible_to: UUID | None,
        scope: MemoryScope | None = None,
        keyword: str | None = None,
        include_inactive: bool = True,
        limit: int = 200,
    ) -> Sequence[Memory]:
        """管理视图列表。

        ``visible_to`` 为普通成员 id 时按可见性裁剪：企业/员工级全量可见
        （运行任务时本就会注入），用户级仅本人，会话级仅本人创建的会话；
        ``visible_to=None`` 表示管理权限视角，返回租户内全部。
        """

        query = select(MemoryRecord).where(MemoryRecord.tenant_id == tenant_id)
        if visible_to is not None:
            own_conversations = (
                select(ConversationRecord.id)
                .where(
                    ConversationRecord.tenant_id == tenant_id,
                    ConversationRecord.created_by == visible_to,
                )
                .scalar_subquery()
            )
            query = query.where(
                or_(
                    MemoryRecord.scope.in_(
                        [MemoryScope.TENANT.value, MemoryScope.EMPLOYEE.value]
                    ),
                    (MemoryRecord.scope == MemoryScope.USER.value)
                    & (MemoryRecord.scope_ref == visible_to),
                    (MemoryRecord.scope == MemoryScope.CONVERSATION.value)
                    & MemoryRecord.scope_ref.in_(own_conversations),
                )
            )
        if scope is not None:
            query = query.where(MemoryRecord.scope == scope.value)
        if keyword:
            query = query.where(MemoryRecord.content.contains(keyword))
        if not include_inactive:
            query = query.where(MemoryRecord.status == MemoryStatus.ACTIVE.value)
        result = await self._session.execute(
            query.order_by(MemoryRecord.updated_at.desc()).limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def search_for_runtime(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        employee_id: UUID,
        conversation_id: UUID | None,
        limit: int = MEMORY_RUNTIME_INJECTION_LIMIT,
        now: datetime | None = None,
    ) -> Sequence[Memory]:
        """运行时按权限召回：active、未过期（读取时判定）、命名空间受限。

        可召回命名空间 = 企业级 + 当前员工 + 发起用户 + 当前会话（如有）。
        按最近性排序并截断，保证注入上下文有界。
        """

        current = now or datetime.now(UTC)
        namespace_filters = [
            (MemoryRecord.scope == MemoryScope.TENANT.value)
            & (MemoryRecord.scope_ref == tenant_id),
            (MemoryRecord.scope == MemoryScope.USER.value)
            & (MemoryRecord.scope_ref == user_id),
            (MemoryRecord.scope == MemoryScope.EMPLOYEE.value)
            & (MemoryRecord.scope_ref == employee_id),
        ]
        if conversation_id is not None:
            namespace_filters.append(
                (MemoryRecord.scope == MemoryScope.CONVERSATION.value)
                & (MemoryRecord.scope_ref == conversation_id)
            )
        result = await self._session.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.status == MemoryStatus.ACTIVE.value,
                or_(
                    MemoryRecord.expires_at.is_(None),
                    MemoryRecord.expires_at > current,
                ),
                or_(*namespace_filters),
            )
            .order_by(MemoryRecord.updated_at.desc())
            .limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def prune_auto_capacity(
        self,
        *,
        tenant_id: UUID,
        scope: MemoryScope,
        scope_ref: UUID,
        capacity: int,
    ) -> int:
        """裁剪命名空间内超出容量的最旧自动来源记忆（长期成本有界）。

        手工（manual）记忆不参与自动裁剪，只能由用户显式删除。
        """

        auto_sources = [MemorySource.RUN.value, MemorySource.CONVERSATION.value]
        keep_ids = (
            select(MemoryRecord.id)
            .where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.scope == scope.value,
                MemoryRecord.scope_ref == scope_ref,
                MemoryRecord.source.in_(auto_sources),
            )
            .order_by(MemoryRecord.updated_at.desc())
            .limit(capacity)
        ).scalar_subquery()
        result = await self._session.execute(
            delete(MemoryRecord).where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.scope == scope.value,
                MemoryRecord.scope_ref == scope_ref,
                MemoryRecord.source.in_(auto_sources),
                MemoryRecord.id.not_in(keep_ids),
            )
        )
        if not isinstance(result, CursorResult):
            return 0
        return int(result.rowcount or 0)

    @staticmethod
    def _to_record(memory: Memory) -> MemoryRecord:
        return MemoryRecord(
            id=memory.id,
            tenant_id=memory.tenant_id,
            scope=memory.scope.value,
            scope_ref=memory.scope_ref,
            key=memory.key,
            content=memory.content,
            source=memory.source.value,
            source_ref=memory.source_ref,
            confidence=memory.confidence,
            status=memory.status.value,
            expires_at=memory.expires_at,
            created_by=memory.created_by,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )

    @staticmethod
    def _to_entity(record: MemoryRecord) -> Memory:
        created_at = _as_utc(record.created_at)
        updated_at = _as_utc(record.updated_at)
        assert created_at is not None and updated_at is not None
        return Memory(
            id=record.id,
            tenant_id=record.tenant_id,
            scope=MemoryScope(record.scope),
            scope_ref=record.scope_ref,
            key=record.key,
            content=record.content,
            source=MemorySource(record.source),
            source_ref=record.source_ref,
            confidence=record.confidence,
            status=MemoryStatus(record.status),
            expires_at=_as_utc(record.expires_at),
            created_by=record.created_by,
            created_at=created_at,
            updated_at=updated_at,
        )
