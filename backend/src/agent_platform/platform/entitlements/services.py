from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agent_platform.platform.entitlements.entities import CapabilityEntitlement
from agent_platform.platform.tenants.memberships import TenantRole

_CAPABILITY_MANAGING_ROLES = frozenset({TenantRole.OWNER, TenantRole.ADMIN})


@dataclass(frozen=True, slots=True)
class CapabilityAvailability:
    """Single source of truth for `installed && entitled && permitted`."""

    deployment_installed: bool
    tenant_entitled: bool
    user_permissions: frozenset[str]

    @property
    def available(self) -> bool:
        return self.deployment_installed and self.tenant_entitled and bool(self.user_permissions)


def capability_permissions_for_role(
    *,
    role: TenantRole,
    manifest_permissions: Iterable[str],
) -> frozenset[str]:
    if role in _CAPABILITY_MANAGING_ROLES:
        return frozenset(manifest_permissions)
    return frozenset()


def evaluate_capability_availability(
    *,
    deployment_installed: bool,
    entitlement: CapabilityEntitlement | None,
    role: TenantRole,
    manifest_permissions: Iterable[str],
    now: datetime,
) -> CapabilityAvailability:
    tenant_entitled = entitlement is not None and entitlement.is_effective(now=now)
    user_permissions = (
        capability_permissions_for_role(role=role, manifest_permissions=manifest_permissions)
        if deployment_installed and tenant_entitled
        else frozenset[str]()
    )
    return CapabilityAvailability(
        deployment_installed=deployment_installed,
        tenant_entitled=tenant_entitled,
        user_permissions=user_permissions,
    )
