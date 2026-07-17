"""企业成员邀请仓储。

接受/拒绝/撤销必须先用 ``get_by_token_digest_for_update`` / ``get_for_update``
取行锁，在锁内做领域状态机转换并落库，防止同一 token 的并发重放。
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.tenants.invitations import (
    InvitationStatus,
    TenantInvitation,
)
from agent_platform.platform.tenants.memberships import TenantRole


class TenantInvitationRecord(Base):
    __tablename__ = "tenant_invitations"
    __table_args__ = (
        # 与迁移 0034 对齐：唯一索引显式命名 uq_...（而非列级 index=True 自动名 ix_...）。
        Index("uq_tenant_invitations_token_digest", "token_digest", unique=True),
        Index("ix_tenant_invitations_tenant_status", "tenant_id", "status"),
        Index("ix_tenant_invitations_email", "email"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))
    token_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    invited_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )


class SqlAlchemyInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invitation: TenantInvitation) -> None:
        self._session.add(_to_record(invitation))
        await self._session.flush()

    async def list_pending(self, tenant_id: UUID) -> list[TenantInvitation]:
        result = await self._session.execute(
            select(TenantInvitationRecord)
            .where(
                TenantInvitationRecord.tenant_id == tenant_id,
                TenantInvitationRecord.status == InvitationStatus.PENDING.value,
            )
            .order_by(TenantInvitationRecord.created_at.desc())
        )
        return [_to_entity(record) for record in result.scalars().all()]

    async def get_by_token_digest_for_update(
        self, token_digest: str
    ) -> TenantInvitation | None:
        result = await self._session.execute(
            select(TenantInvitationRecord)
            .where(TenantInvitationRecord.token_digest == token_digest)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        return _to_entity(record) if record is not None else None

    async def get_for_update(
        self, *, tenant_id: UUID, invitation_id: UUID
    ) -> TenantInvitation | None:
        result = await self._session.execute(
            select(TenantInvitationRecord)
            .where(
                TenantInvitationRecord.id == invitation_id,
                TenantInvitationRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        return _to_entity(record) if record is not None else None

    async def find_pending_for_email(
        self, *, tenant_id: UUID, email: str
    ) -> TenantInvitation | None:
        result = await self._session.execute(
            select(TenantInvitationRecord).where(
                TenantInvitationRecord.tenant_id == tenant_id,
                TenantInvitationRecord.email == email.strip().lower(),
                TenantInvitationRecord.status == InvitationStatus.PENDING.value,
            )
        )
        record = result.scalars().first()
        return _to_entity(record) if record is not None else None

    async def save(self, invitation: TenantInvitation) -> None:
        record = await self._session.get(TenantInvitationRecord, invitation.id)
        if record is None:
            raise RuntimeError("invitation record disappeared before save")
        record.status = invitation.status.value
        record.responded_at = invitation.responded_at
        record.accepted_by = invitation.accepted_by
        await self._session.flush()


def _to_record(invitation: TenantInvitation) -> TenantInvitationRecord:
    return TenantInvitationRecord(
        id=invitation.id,
        tenant_id=invitation.tenant_id,
        email=invitation.email,
        role=invitation.role.value,
        token_digest=invitation.token_digest,
        status=invitation.status.value,
        invited_by=invitation.invited_by,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        responded_at=invitation.responded_at,
        accepted_by=invitation.accepted_by,
    )


def _to_entity(record: TenantInvitationRecord) -> TenantInvitation:
    return TenantInvitation(
        id=record.id,
        tenant_id=record.tenant_id,
        email=record.email,
        role=TenantRole(record.role),
        token_digest=record.token_digest,
        status=InvitationStatus(record.status),
        invited_by=record.invited_by,
        created_at=_as_utc(record.created_at),
        expires_at=_as_utc(record.expires_at),
        responded_at=_as_utc(record.responded_at) if record.responded_at else None,
        accepted_by=record.accepted_by,
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
