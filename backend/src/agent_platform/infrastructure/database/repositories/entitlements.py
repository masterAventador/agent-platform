from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.entitlements.entities import (
    CapabilityEntitlement,
    EntitlementStatus,
)

_WRITE_ATTEMPTS = 3


class EntitlementWriteConflict(RuntimeError):
    """Concurrent writers kept invalidating the compare-and-swap window."""


class CapabilityEntitlementRecord(Base):
    __tablename__ = "capability_entitlements"
    __table_args__ = (
        Index(
            "uq_capability_entitlements_tenant_capability",
            "tenant_id",
            "capability_id",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("tenants.id"))
    capability_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)


class SqlAlchemyCapabilityEntitlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        *,
        tenant_id: UUID,
        capability_id: str,
    ) -> CapabilityEntitlement | None:
        record = await self._load(tenant_id=tenant_id, capability_id=capability_id)
        return None if record is None else _to_entity(record)

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[CapabilityEntitlement]:
        records = await self._session.scalars(
            select(CapabilityEntitlementRecord)
            .where(CapabilityEntitlementRecord.tenant_id == tenant_id)
            .order_by(CapabilityEntitlementRecord.capability_id)
        )
        return [_to_entity(record) for record in records]

    async def grant(
        self,
        *,
        tenant_id: UUID,
        capability_id: str,
        granted_by: UUID | None,
        source: str,
        expires_at: datetime | None,
        now: datetime,
    ) -> CapabilityEntitlement:
        for _ in range(_WRITE_ATTEMPTS):
            record = await self._load(tenant_id=tenant_id, capability_id=capability_id)
            if record is None:
                created = CapabilityEntitlementRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    capability_id=capability_id,
                    status=EntitlementStatus.ACTIVE.value,
                    source=source,
                    expires_at=expires_at,
                    granted_at=now,
                    granted_by=granted_by,
                    revoked_at=None,
                    revoked_by=None,
                    revision=1,
                )
                try:
                    async with self._session.begin_nested():
                        self._session.add(created)
                except IntegrityError:
                    continue
                return _to_entity(created)

            next_revision = record.revision + 1
            result = await self._session.execute(
                update(CapabilityEntitlementRecord)
                .where(
                    CapabilityEntitlementRecord.id == record.id,
                    CapabilityEntitlementRecord.revision == record.revision,
                )
                .values(
                    status=EntitlementStatus.ACTIVE.value,
                    source=source,
                    expires_at=expires_at,
                    granted_at=now,
                    granted_by=granted_by,
                    revoked_at=None,
                    revoked_by=None,
                    revision=next_revision,
                )
            )
            if isinstance(result, CursorResult) and result.rowcount == 1:
                refreshed = await self._load(tenant_id=tenant_id, capability_id=capability_id)
                if refreshed is None:
                    raise EntitlementWriteConflict("entitlement disappeared during grant")
                return _to_entity(refreshed)
            self._session.expire_all()
        raise EntitlementWriteConflict("could not grant entitlement after concurrent writes")

    async def revoke(
        self,
        *,
        tenant_id: UUID,
        capability_id: str,
        revoked_by: UUID | None,
        now: datetime,
    ) -> CapabilityEntitlement | None:
        for _ in range(_WRITE_ATTEMPTS):
            record = await self._load(tenant_id=tenant_id, capability_id=capability_id)
            if record is None:
                return None
            if record.status == EntitlementStatus.REVOKED.value:
                return _to_entity(record)

            result = await self._session.execute(
                update(CapabilityEntitlementRecord)
                .where(
                    CapabilityEntitlementRecord.id == record.id,
                    CapabilityEntitlementRecord.revision == record.revision,
                )
                .values(
                    status=EntitlementStatus.REVOKED.value,
                    revoked_at=now,
                    revoked_by=revoked_by,
                    revision=record.revision + 1,
                )
            )
            if isinstance(result, CursorResult) and result.rowcount == 1:
                refreshed = await self._load(tenant_id=tenant_id, capability_id=capability_id)
                if refreshed is None:
                    raise EntitlementWriteConflict("entitlement disappeared during revoke")
                return _to_entity(refreshed)
            self._session.expire_all()
        raise EntitlementWriteConflict("could not revoke entitlement after concurrent writes")

    async def _load(
        self,
        *,
        tenant_id: UUID,
        capability_id: str,
    ) -> CapabilityEntitlementRecord | None:
        record = await self._session.scalar(
            select(CapabilityEntitlementRecord).where(
                CapabilityEntitlementRecord.tenant_id == tenant_id,
                CapabilityEntitlementRecord.capability_id == capability_id,
            )
        )
        return record


def _to_entity(record: CapabilityEntitlementRecord) -> CapabilityEntitlement:
    return CapabilityEntitlement(
        id=record.id,
        tenant_id=record.tenant_id,
        capability_id=record.capability_id,
        status=EntitlementStatus(record.status),
        source=record.source,
        expires_at=_as_utc(record.expires_at),
        granted_at=_require_utc(record.granted_at),
        granted_by=record.granted_by,
        revoked_at=_as_utc(record.revoked_at),
        revoked_by=record.revoked_by,
        revision=record.revision,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _require_utc(value)


def _require_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
