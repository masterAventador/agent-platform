from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from agent_platform.platform.model_gateway.entities import (
    ModelGatewayPolicyStatus,
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)


class ModelGatewayProvisioningAction(StrEnum):
    # 唯一动作：对账把 desired（策略 + 当前 Key 版本）落到真实网关。轮换不是独立动作，
    # 它同时递增 Key 版本与策略 revision，再由同一条 reconcile 命令收敛。
    RECONCILE = "reconcile"


class ProvisioningCommandStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ClaimedProvisioningCommand:
    """已被本副本独占认领的一条 outbox 命令及其当前 desired 状态。"""

    command_id: UUID
    tenant_id: UUID
    desired_revision: int
    action: ModelGatewayProvisioningAction
    attempts: int
    policy: TenantModelGatewayPolicy
    key: TenantModelGatewayKey


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """对账决策结果；由 store 在同一事务内落库。

    ``key_provisioned`` 是本次对账对「网关侧是否存在可用 Key」的观测：``True`` 已建立、
    ``False`` 已阻断、``None`` 本次未能观测（失败/重试）因此保持既有观测不变。
    """

    command_status: ProvisioningCommandStatus
    policy_status: ModelGatewayPolicyStatus | None = None
    error_code: str | None = None
    next_attempt_at: datetime | None = None
    key_provisioned: bool | None = None
    clear_key_retirement: bool = False


ProvisioningHandler = Callable[[ClaimedProvisioningCommand], Awaitable[ReconcileOutcome]]


class ModelGatewayPolicyRepository(Protocol):
    async def get(self, tenant_id: UUID) -> TenantModelGatewayPolicy | None: ...

    async def save_desired(
        self,
        policy: TenantModelGatewayPolicy,
        *,
        expected_revision: int,
        action: ModelGatewayProvisioningAction,
    ) -> None: ...

    async def get_key(self, tenant_id: UUID) -> TenantModelGatewayKey | None: ...

    async def save_rotated_key(
        self,
        policy: TenantModelGatewayPolicy,
        *,
        key: TenantModelGatewayKey,
        expected_revision: int,
        action: ModelGatewayProvisioningAction,
    ) -> None: ...


class ModelGatewayCommandStore(Protocol):
    """outbox 认领与结算；事务边界完全由实现持有。"""

    async def process_next(self, handler: ProvisioningHandler, *, now: datetime) -> bool: ...

    async def prune_settled(self, *, older_than: datetime, limit: int) -> int: ...


class ModelGatewayProvisioner(Protocol):
    """把 desired 状态对账到真实模型网关的公开管理接口。"""

    async def apply_enabled(
        self, *, policy: TenantModelGatewayPolicy, key: TenantModelGatewayKey
    ) -> None: ...

    async def apply_disabled(
        self, *, policy: TenantModelGatewayPolicy, key: TenantModelGatewayKey
    ) -> None: ...
