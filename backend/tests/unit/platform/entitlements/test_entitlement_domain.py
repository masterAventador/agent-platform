from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.entitlements.entities import (
    CapabilityEntitlement,
    EntitlementStatus,
    EntitlementValidationError,
    validate_capability_id,
    validate_entitlement_source,
    validate_expiry,
)
from agent_platform.platform.entitlements.services import (
    capability_permissions_for_role,
    evaluate_capability_availability,
)
from agent_platform.platform.tenants.memberships import TenantRole

_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
_PERMISSIONS = ("social.read", "social.manage", "social.execute")


def _entitlement(
    *,
    status: EntitlementStatus = EntitlementStatus.ACTIVE,
    expires_at: datetime | None = None,
) -> CapabilityEntitlement:
    return CapabilityEntitlement(
        id=uuid4(),
        tenant_id=uuid4(),
        capability_id="social-operations",
        status=status,
        source="manual",
        expires_at=expires_at,
        granted_at=_NOW - timedelta(days=1),
        granted_by=uuid4(),
        revoked_at=None,
        revoked_by=None,
        revision=1,
    )


class TestEntitlementEffectiveness:
    def test_active_without_expiry_is_effective(self) -> None:
        assert _entitlement().is_effective(now=_NOW)

    def test_active_with_future_expiry_is_effective(self) -> None:
        entitlement = _entitlement(expires_at=_NOW + timedelta(minutes=1))
        assert entitlement.is_effective(now=_NOW)

    def test_active_with_past_expiry_is_not_effective(self) -> None:
        entitlement = _entitlement(expires_at=_NOW - timedelta(seconds=1))
        assert not entitlement.is_effective(now=_NOW)

    def test_expiry_boundary_is_exclusive(self) -> None:
        entitlement = _entitlement(expires_at=_NOW)
        assert not entitlement.is_effective(now=_NOW)

    def test_revoked_is_never_effective(self) -> None:
        entitlement = _entitlement(
            status=EntitlementStatus.REVOKED,
            expires_at=_NOW + timedelta(days=30),
        )
        assert not entitlement.is_effective(now=_NOW)


class TestEntitlementValidation:
    def test_rejects_invalid_capability_id(self) -> None:
        for capability_id in ("", "Social", "social_ops", "-social", "social-", "a b"):
            with pytest.raises(EntitlementValidationError):
                validate_capability_id(capability_id)

    def test_accepts_canonical_capability_id(self) -> None:
        validate_capability_id("social-operations")
        validate_capability_id("video-studio")

    def test_rejects_naive_or_past_expiry_on_grant(self) -> None:
        with pytest.raises(EntitlementValidationError):
            validate_expiry(datetime(2027, 1, 1), now=_NOW)
        with pytest.raises(EntitlementValidationError):
            validate_expiry(_NOW - timedelta(seconds=1), now=_NOW)
        with pytest.raises(EntitlementValidationError):
            validate_expiry(_NOW, now=_NOW)

    def test_accepts_future_or_absent_expiry(self) -> None:
        validate_expiry(None, now=_NOW)
        validate_expiry(_NOW + timedelta(days=30), now=_NOW)

    def test_rejects_invalid_source(self) -> None:
        for source in ("", " ", "a" * 65, "bad source\n"):
            with pytest.raises(EntitlementValidationError):
                validate_entitlement_source(source)

    def test_accepts_canonical_source(self) -> None:
        validate_entitlement_source("manual")
        validate_entitlement_source("demo-seed")


class TestCapabilityPermissionsForRole:
    def test_owner_and_admin_receive_all_manifest_permissions(self) -> None:
        for role in (TenantRole.OWNER, TenantRole.ADMIN):
            assert capability_permissions_for_role(
                role=role,
                manifest_permissions=_PERMISSIONS,
            ) == frozenset(_PERMISSIONS)

    def test_member_receives_no_capability_permissions(self) -> None:
        assert (
            capability_permissions_for_role(
                role=TenantRole.MEMBER,
                manifest_permissions=_PERMISSIONS,
            )
            == frozenset()
        )


class TestEvaluateCapabilityAvailability:
    def test_available_only_when_all_three_layers_pass(self) -> None:
        availability = evaluate_capability_availability(
            deployment_installed=True,
            entitlement=_entitlement(),
            role=TenantRole.OWNER,
            manifest_permissions=_PERMISSIONS,
            now=_NOW,
        )
        assert availability.deployment_installed
        assert availability.tenant_entitled
        assert availability.user_permissions == frozenset(_PERMISSIONS)
        assert availability.available

    def test_not_installed_fails_closed(self) -> None:
        availability = evaluate_capability_availability(
            deployment_installed=False,
            entitlement=_entitlement(),
            role=TenantRole.OWNER,
            manifest_permissions=_PERMISSIONS,
            now=_NOW,
        )
        assert not availability.deployment_installed
        assert not availability.available

    def test_missing_entitlement_fails_closed(self) -> None:
        availability = evaluate_capability_availability(
            deployment_installed=True,
            entitlement=None,
            role=TenantRole.OWNER,
            manifest_permissions=_PERMISSIONS,
            now=_NOW,
        )
        assert not availability.tenant_entitled
        assert not availability.available

    def test_revoked_or_expired_entitlement_fails_closed(self) -> None:
        for entitlement in (
            _entitlement(status=EntitlementStatus.REVOKED),
            _entitlement(expires_at=_NOW - timedelta(seconds=1)),
        ):
            availability = evaluate_capability_availability(
                deployment_installed=True,
                entitlement=entitlement,
                role=TenantRole.OWNER,
                manifest_permissions=_PERMISSIONS,
                now=_NOW,
            )
            assert not availability.tenant_entitled
            assert not availability.available

    def test_member_without_permissions_is_not_available(self) -> None:
        availability = evaluate_capability_availability(
            deployment_installed=True,
            entitlement=_entitlement(),
            role=TenantRole.MEMBER,
            manifest_permissions=_PERMISSIONS,
            now=_NOW,
        )
        assert availability.tenant_entitled
        assert availability.user_permissions == frozenset()
        assert not availability.available
