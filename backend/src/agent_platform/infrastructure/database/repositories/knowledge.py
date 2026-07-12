from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.knowledge.entities import KnowledgeBase


class KnowledgeBaseRecord(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(4000))
    provider: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[str] = mapped_column(String(200), unique=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_knowledge_bases_tenant_name", tenant_id, name, unique=True),)


class SqlAlchemyKnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, knowledge_base: KnowledgeBase) -> None:
        self._session.add(
            KnowledgeBaseRecord(
                id=knowledge_base.id,
                tenant_id=knowledge_base.tenant_id,
                name=knowledge_base.name,
                description=knowledge_base.description,
                provider=knowledge_base.provider,
                provider_id=knowledge_base.provider_id,
                created_by=knowledge_base.created_by,
                created_at=knowledge_base.created_at,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase | None:
        result = await self._session.execute(
            select(KnowledgeBaseRecord).where(
                KnowledgeBaseRecord.id == knowledge_base_id,
                KnowledgeBaseRecord.tenant_id == tenant_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._entity(record) if record is not None else None

    async def list(self, *, tenant_id: UUID) -> list[KnowledgeBase]:
        result = await self._session.execute(
            select(KnowledgeBaseRecord)
            .where(KnowledgeBaseRecord.tenant_id == tenant_id)
            .order_by(KnowledgeBaseRecord.created_at.desc())
        )
        return [self._entity(record) for record in result.scalars()]

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        record = await self._session.get(KnowledgeBaseRecord, knowledge_base.id)
        if record is not None:
            await self._session.delete(record)
            await self._session.flush()

    @staticmethod
    def _entity(record: KnowledgeBaseRecord) -> KnowledgeBase:
        created_at = (
            record.created_at
            if record.created_at.tzinfo is not None
            else record.created_at.replace(tzinfo=UTC)
        )
        return KnowledgeBase(
            id=record.id,
            tenant_id=record.tenant_id,
            name=record.name,
            description=record.description,
            provider=record.provider,
            provider_id=record.provider_id,
            created_by=record.created_by,
            created_at=created_at,
        )
