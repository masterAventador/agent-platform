from datetime import UTC, datetime
from uuid import UUID

from agent_platform.platform.model_gateway.entities import (
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayKeyNotProvisioned,
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

    async def rotate_key(
        self,
        *,
        tenant_id: UUID,
        rotated_by: UUID,
    ) -> tuple[TenantModelGatewayPolicy, TenantModelGatewayKey]:
        """轮换租户虚拟 Key。

        轮换是一次 desired 状态变更：先原子地递增 Key 版本、记下待回收的旧版本，并把策略
        推回 pending + 新 revision，再由 Controller 用同一条 reconcile 命令收敛到网关。
        版本必须先落库再触达网关——反过来会在崩溃时留下网关侧无人回收的孤儿 Key。
        """
        current = await self._repository.get(tenant_id)
        if current is None:
            raise ModelGatewayPolicyNotFound
        key = await self._repository.get_key(tenant_id)
        if key is None:
            # 首次对账才签发 Key：尚未签发时没有可轮换的对象。
            raise ModelGatewayKeyNotProvisioned
        rotated = key.rotate(now=datetime.now(UTC))
        desired = current.revise_desired(
            enabled=current.enabled,
            allowed_aliases=current.allowed_aliases,
            budget_microusd=current.budget_microusd,
            budget_period=current.budget_period,
            rpm_limit=current.rpm_limit,
            tpm_limit=current.tpm_limit,
            max_parallel_requests=current.max_parallel_requests,
            updated_by=rotated_by,
            now=datetime.now(UTC),
        )
        await self._repository.save_rotated_key(
            desired,
            key=rotated,
            expected_revision=current.revision,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        return desired, rotated
