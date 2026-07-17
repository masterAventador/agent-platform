from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.tenants.errors import (
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotPending,
    InvitationRoleNotAllowed,
)
from agent_platform.platform.tenants.invitations import (
    InvitationStatus,
    TenantInvitation,
)
from agent_platform.platform.tenants.memberships import TenantRole

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _issue(
    role: TenantRole = TenantRole.MEMBER,
    email: str = "invitee@example.com",
) -> TenantInvitation:
    return TenantInvitation.issue(
        tenant_id=uuid4(),
        email=email,
        role=role,
        token_digest="digest-abc",
        invited_by=uuid4(),
        ttl_seconds=7 * 24 * 3600,
        now=NOW,
    )


def test_issue_normalizes_email_and_sets_pending() -> None:
    invitation = TenantInvitation.issue(
        tenant_id=uuid4(),
        email="  Invitee@Example.COM ",
        role=TenantRole.ADMIN,
        token_digest="d",
        invited_by=uuid4(),
        ttl_seconds=3600,
        now=NOW,
    )
    assert invitation.email == "invitee@example.com"
    assert invitation.status is InvitationStatus.PENDING
    assert invitation.expires_at == NOW + timedelta(seconds=3600)
    assert invitation.is_pending(now=NOW)


def test_issue_rejects_owner_role() -> None:
    with pytest.raises(InvitationRoleNotAllowed):
        _issue(role=TenantRole.OWNER)


def test_accept_matches_email_case_insensitively() -> None:
    invitation = _issue(email="invitee@example.com")
    user_id = uuid4()
    accepted = invitation.accept(
        user_id=user_id,
        user_email="INVITEE@example.com",
        now=NOW + timedelta(hours=1),
    )
    assert accepted.status is InvitationStatus.ACCEPTED
    assert accepted.accepted_by == user_id
    assert accepted.responded_at == NOW + timedelta(hours=1)


def test_accept_with_mismatched_email_is_rejected() -> None:
    invitation = _issue(email="invitee@example.com")
    with pytest.raises(InvitationEmailMismatch):
        invitation.accept(user_id=uuid4(), user_email="other@example.com", now=NOW)


def test_accept_expired_invitation_is_rejected() -> None:
    invitation = _issue()
    with pytest.raises(InvitationExpired):
        invitation.accept(
            user_id=uuid4(),
            user_email="invitee@example.com",
            now=NOW + timedelta(days=30),
        )


def test_accept_already_accepted_is_rejected_as_not_pending() -> None:
    invitation = _issue()
    accepted = invitation.accept(user_id=uuid4(), user_email="invitee@example.com", now=NOW)
    with pytest.raises(InvitationNotPending):
        accepted.accept(user_id=uuid4(), user_email="invitee@example.com", now=NOW)


def test_reject_moves_to_rejected() -> None:
    invitation = _issue()
    rejected = invitation.reject(now=NOW)
    assert rejected.status is InvitationStatus.REJECTED


def test_revoke_a_pending_invitation() -> None:
    invitation = _issue()
    revoked = invitation.revoke(now=NOW)
    assert revoked.status is InvitationStatus.REVOKED


def test_revoke_non_pending_is_rejected() -> None:
    invitation = _issue()
    revoked = invitation.revoke(now=NOW)
    with pytest.raises(InvitationNotPending):
        revoked.revoke(now=NOW)


def test_expired_pending_invitation_is_not_pending() -> None:
    invitation = _issue()
    assert not invitation.is_pending(now=NOW + timedelta(days=30))
