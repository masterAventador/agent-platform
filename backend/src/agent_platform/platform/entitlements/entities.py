from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

_CAPABILITY_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SOURCE_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$")
_SOURCE_MAX_LENGTH = 64


class EntitlementValidationError(ValueError):
    """A capability entitlement request violates the domain contract."""


class EntitlementStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class CapabilityEntitlement:
    """Tenant-level authorization record for one optional capability."""

    id: UUID
    tenant_id: UUID
    capability_id: str
    status: EntitlementStatus
    source: str
    expires_at: datetime | None
    granted_at: datetime
    granted_by: UUID | None
    revoked_at: datetime | None
    revoked_by: UUID | None
    revision: int

    def is_effective(self, *, now: datetime) -> bool:
        if self.status is not EntitlementStatus.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > now


def validate_capability_id(capability_id: str) -> None:
    if (
        not isinstance(capability_id, str)
        or _CAPABILITY_ID_PATTERN.fullmatch(capability_id) is None
    ):
        raise EntitlementValidationError("invalid capability id")


def validate_entitlement_source(source: str) -> None:
    if (
        not isinstance(source, str)
        or len(source) > _SOURCE_MAX_LENGTH
        or _SOURCE_PATTERN.fullmatch(source) is None
    ):
        raise EntitlementValidationError("invalid entitlement source")


def validate_expiry(expires_at: datetime | None, *, now: datetime) -> None:
    if expires_at is None:
        return
    if expires_at.tzinfo is None:
        raise EntitlementValidationError("entitlement expiry must be timezone-aware")
    if expires_at <= now:
        raise EntitlementValidationError("entitlement expiry must be in the future")
