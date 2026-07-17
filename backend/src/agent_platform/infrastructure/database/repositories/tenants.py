from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.tenants.errors import TenantSlugAlreadyExists
from agent_platform.platform.tenants.memberships import (
    TenantMembership,
    TenantRole,
    WorkspaceAccess,
)
from agent_platform.platform.users.entities import User


class TenantRecord(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantMembershipRecord(Base):
    __tablename__ = "tenant_memberships"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)


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
            await self._session.flush()
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

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        record = await self._session.get(TenantRecord, tenant_id)
        if record is None:
            return None
        return Tenant(
            id=record.id,
            name=record.name,
            slug=record.slug,
            created_at=(
                record.created_at
                if record.created_at.tzinfo is not None
                else record.created_at.replace(tzinfo=UTC)
            ),
        )

    async def rename(self, *, tenant_id: UUID, name: str) -> None:
        await self._session.execute(
            update(TenantRecord)
            .where(TenantRecord.id == tenant_id)
            .values(name=name.strip())
        )
        await self._session.flush()


class SqlAlchemyWorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def provision_owner_workspace(self, user: User) -> WorkspaceAccess:
        tenant = Tenant.create(
            name=f"{user.email.split('@', maxsplit=1)[0]} 的工作区",
            slug=f"workspace-{user.id.hex}",
        )
        membership = TenantMembership.create_owner(tenant_id=tenant.id, user_id=user.id)
        self._session.add(
            TenantRecord(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                created_at=tenant.created_at,
            )
        )
        await self._session.flush()
        self._session.add(
            TenantMembershipRecord(
                id=membership.id,
                tenant_id=membership.tenant_id,
                user_id=membership.user_id,
                role=membership.role.value,
                created_at=membership.created_at,
            )
        )
        await self._session.flush()
        return WorkspaceAccess(tenant=tenant, role=membership.role)

    async def list_for_user(self, user_id: UUID) -> list[WorkspaceAccess]:
        result = await self._session.execute(
            select(TenantRecord, TenantMembershipRecord)
            .join(
                TenantMembershipRecord,
                TenantMembershipRecord.tenant_id == TenantRecord.id,
            )
            .where(TenantMembershipRecord.user_id == user_id)
            .order_by(TenantMembershipRecord.created_at)
        )
        return [
            WorkspaceAccess(
                tenant=Tenant(
                    id=tenant.id,
                    name=tenant.name,
                    slug=tenant.slug,
                    created_at=self._as_utc(tenant.created_at),
                ),
                role=TenantRole(membership.role),
            )
            for tenant, membership in result.all()
        ]

    async def get_for_user(self, *, user_id: UUID, tenant_id: UUID) -> WorkspaceAccess | None:
        result = await self._session.execute(
            select(TenantRecord, TenantMembershipRecord)
            .join(
                TenantMembershipRecord,
                TenantMembershipRecord.tenant_id == TenantRecord.id,
            )
            .where(
                TenantMembershipRecord.user_id == user_id,
                TenantMembershipRecord.tenant_id == tenant_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        tenant, membership = row
        return WorkspaceAccess(
            tenant=Tenant(
                id=tenant.id,
                name=tenant.name,
                slug=tenant.slug,
                created_at=self._as_utc(tenant.created_at),
            ),
            role=TenantRole(membership.role),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
