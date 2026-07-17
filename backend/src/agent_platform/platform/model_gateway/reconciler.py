"""模型网关 Provisioning Controller 的对账决策。

只做决策：认领/结算的事务边界属 ``ModelGatewayCommandStore``，真实网关调用属
``ModelGatewayProvisioner``。本层保证状态推进、错误分类、有界退避，以及
「结果不确定绝不自动重放」——语义与 Tool Gateway 的 ``tool_execution_uncertain`` 一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agent_platform.platform.model_gateway.entities import ModelGatewayPolicyStatus
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayProvisioningError,
    ModelGatewayProvisioningOutcomeUnknown,
    ModelGatewayProvisioningPermanent,
    ModelGatewayProvisioningTransient,
)
from agent_platform.platform.model_gateway.ports import (
    ClaimedProvisioningCommand,
    ModelGatewayCommandStore,
    ModelGatewayProvisioner,
    ProvisioningCommandStatus,
    ReconcileOutcome,
)

DEFAULT_MAX_PROVISIONING_ATTEMPTS = 8
DEFAULT_RETRY_BASE_SECONDS = 2.0
DEFAULT_RETRY_CAP_SECONDS = 300.0


class ModelGatewayReconciler:
    def __init__(
        self,
        *,
        store: ModelGatewayCommandStore,
        provisioner: ModelGatewayProvisioner,
        max_attempts: int = DEFAULT_MAX_PROVISIONING_ATTEMPTS,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        retry_cap_seconds: float = DEFAULT_RETRY_CAP_SECONDS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._store = store
        self._provisioner = provisioner
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_cap_seconds = retry_cap_seconds

    async def reconcile_once(self, *, now: datetime) -> bool:
        async def handler(claimed: ClaimedProvisioningCommand) -> ReconcileOutcome:
            return await self._reconcile(claimed, now=now)

        return await self._store.process_next(handler, now=now)

    async def prune_settled_commands(
        self,
        *,
        now: datetime,
        retention: timedelta,
        limit: int,
    ) -> int:
        return await self._store.prune_settled(older_than=now - retention, limit=limit)

    async def _reconcile(
        self,
        claimed: ClaimedProvisioningCommand,
        *,
        now: datetime,
    ) -> ReconcileOutcome:
        try:
            if claimed.policy.enabled:
                await self._provisioner.apply_enabled(policy=claimed.policy, key=claimed.key)
                reached = ModelGatewayPolicyStatus.ACTIVE
            else:
                await self._provisioner.apply_disabled(policy=claimed.policy, key=claimed.key)
                reached = ModelGatewayPolicyStatus.DISABLED
        except ModelGatewayProvisioningOutcomeUnknown as error:
            # 可能已在网关生效：自动重放会产生重复副作用，必须停在可诊断的终态。
            return self._settle_error(error)
        except ModelGatewayProvisioningPermanent as error:
            return self._settle_error(error)
        except ModelGatewayProvisioningTransient as error:
            return self._retry_or_exhaust(claimed, now=now, error=error)
        return ReconcileOutcome(
            command_status=ProvisioningCommandStatus.COMPLETED,
            policy_status=reached,
            clear_key_retirement=True,
        )

    def _retry_or_exhaust(
        self,
        claimed: ClaimedProvisioningCommand,
        *,
        now: datetime,
        error: ModelGatewayProvisioningTransient,
    ) -> ReconcileOutcome:
        attempts = claimed.attempts + 1
        if attempts >= self._max_attempts:
            return ReconcileOutcome(
                command_status=ProvisioningCommandStatus.FAILED,
                policy_status=ModelGatewayPolicyStatus.ERROR,
                error_code="provisioning_retry_exhausted",
            )
        delay = min(
            self._retry_base_seconds * (2 ** claimed.attempts),
            self._retry_cap_seconds,
        )
        # 瞬态失败保持 pending：策略状态不推进，租户看到的仍是「尚未对账」而不是错误终态。
        return ReconcileOutcome(
            command_status=ProvisioningCommandStatus.PENDING,
            error_code=error.code,
            next_attempt_at=now + timedelta(seconds=delay),
        )

    @staticmethod
    def _settle_error(error: ModelGatewayProvisioningError) -> ReconcileOutcome:
        return ReconcileOutcome(
            command_status=ProvisioningCommandStatus.FAILED,
            policy_status=ModelGatewayPolicyStatus.ERROR,
            error_code=error.code,
        )
