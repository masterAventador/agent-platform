from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Uuid, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.tenants.errors import TenantSlugAlreadyExists


class TenantRecord(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SqlAlchemyTenantRepository:
    """基于 SQLAlchemy 的租户仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        self._session.add(
            TenantRecord(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                created_at=tenant.created_at,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError as error:
            await self._session.rollback()
            raise TenantSlugAlreadyExists(tenant.slug) from error

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(
            select(TenantRecord).where(TenantRecord.slug == slug.strip().lower())
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None

        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return Tenant(
            id=record.id,
            name=record.name,
            slug=record.slug,
            created_at=created_at,
        )
