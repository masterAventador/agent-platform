from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from agent_platform.platform.tenants.entities import Tenant


class TenantRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class TenantMembership:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    created_at: datetime

    @classmethod
    def create_owner(cls, *, tenant_id: UUID, user_id: UUID) -> "TenantMembership":
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            role=TenantRole.OWNER,
            created_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    tenant: Tenant
    role: TenantRole
