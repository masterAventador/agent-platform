from enum import StrEnum
from typing import Protocol
from uuid import UUID

from agent_platform.platform.model_gateway.entities import TenantModelGatewayPolicy


class ModelGatewayProvisioningAction(StrEnum):
    RECONCILE = "reconcile"


class ModelGatewayPolicyRepository(Protocol):
    async def get(self, tenant_id: UUID) -> TenantModelGatewayPolicy | None: ...

    async def save_desired(
        self,
        policy: TenantModelGatewayPolicy,
        *,
        expected_revision: int,
        action: ModelGatewayProvisioningAction,
    ) -> None: ...
