"""企业成员邀请领域实体。

邀请是受控的一次性 token：处于 ``PENDING`` 且未过期时可被接受/拒绝/撤销，
任何终态转换后不可再次转换（防重放）。Owner 角色不能通过邀请直接授予。
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from agent_platform.platform.tenants.errors import (
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotPending,
)
from agent_platform.platform.tenants.member_management import ensure_role_is_invitable
from agent_platform.platform.tenants.memberships import TenantRole


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVOKED = "revoked"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TenantInvitation:
    id: UUID
    tenant_id: UUID
    email: str
    role: TenantRole
    token_digest: str
    status: InvitationStatus
    invited_by: UUID
    created_at: datetime
    expires_at: datetime
    responded_at: datetime | None = None
    accepted_by: UUID | None = None

    @classmethod
    def issue(
        cls,
        *,
        tenant_id: UUID,
        email: str,
        role: TenantRole,
        token_digest: str,
        invited_by: UUID,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> "TenantInvitation":
        ensure_role_is_invitable(role)
        created_at = _now(now)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            email=email.strip().lower(),
            role=role,
            token_digest=token_digest,
            status=InvitationStatus.PENDING,
            invited_by=invited_by,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=ttl_seconds),
        )

    def is_pending(self, *, now: datetime | None = None) -> bool:
        return self.status is InvitationStatus.PENDING and self.expires_at > _now(now)

    def _ensure_actionable(self, now: datetime) -> None:
        if self.status is not InvitationStatus.PENDING:
            raise InvitationNotPending
        if self.expires_at <= now:
            raise InvitationExpired

    def accept(
        self,
        *,
        user_id: UUID,
        user_email: str,
        now: datetime | None = None,
    ) -> "TenantInvitation":
        current = _now(now)
        self._ensure_actionable(current)
        if self.email != user_email.strip().lower():
            raise InvitationEmailMismatch
        return replace(
            self,
            status=InvitationStatus.ACCEPTED,
            responded_at=current,
            accepted_by=user_id,
        )

    def reject(self, *, now: datetime | None = None) -> "TenantInvitation":
        current = _now(now)
        self._ensure_actionable(current)
        return replace(self, status=InvitationStatus.REJECTED, responded_at=current)

    def revoke(self, *, now: datetime | None = None) -> "TenantInvitation":
        if self.status is not InvitationStatus.PENDING:
            raise InvitationNotPending
        return replace(self, status=InvitationStatus.REVOKED, responded_at=_now(now))
