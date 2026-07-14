from datetime import UTC, datetime
from uuid import UUID

from agent_platform.platform.model_gateway.entities import TenantModelGatewayPolicy
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayPolicyNotFound,
    ModelGatewayPolicyRevisionConflict,
)
from agent_platform.platform.model_gateway.ports import (
    ModelGatewayPolicyRepository,
    ModelGatewayProvisioningAction,
)


class ModelGatewayPolicyService:
    def __init__(self, repository: ModelGatewayPolicyRepository) -> None:
        self._repository = repository

    async def get(self, tenant_id: UUID) -> TenantModelGatewayPolicy:
        policy = await self._repository.get(tenant_id)
        if policy is None:
            raise ModelGatewayPolicyNotFound
        return policy

    async def put_desired(
        self,
        *,
        tenant_id: UUID,
        updated_by: UUID,
        expected_revision: int,
        enabled: bool,
        allowed_aliases: set[str],
        budget_microusd: int,
        budget_period: str,
        rpm_limit: int,
        tpm_limit: int,
        max_parallel_requests: int,
    ) -> TenantModelGatewayPolicy:
        current = await self._repository.get(tenant_id)
        if current is None:
            if expected_revision != 0:
                raise ModelGatewayPolicyRevisionConflict
            desired = TenantModelGatewayPolicy.create_desired(
                tenant_id=tenant_id,
                enabled=enabled,
                allowed_aliases=allowed_aliases,
                budget_microusd=budget_microusd,
                budget_period=budget_period,
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                max_parallel_requests=max_parallel_requests,
                revision=1,
                updated_by=updated_by,
                now=datetime.now(UTC),
            )
        else:
            if current.revision != expected_revision:
                raise ModelGatewayPolicyRevisionConflict
            desired = current.revise_desired(
                enabled=enabled,
                allowed_aliases=allowed_aliases,
                budget_microusd=budget_microusd,
                budget_period=budget_period,
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                max_parallel_requests=max_parallel_requests,
                updated_by=updated_by,
                now=datetime.now(UTC),
            )
        await self._repository.save_desired(
            desired,
            expected_revision=expected_revision,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        return desired
