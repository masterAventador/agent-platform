"""按租户解析可归因的模型网关凭据。

Worker 每次运行都用本模块把租户状态翻译成该租户专属的虚拟 Key，取代此前应用级共享 Key。
绝不回退共享凭据——回退等于让调用重新变得不可归因。

**「对账进度」与「凭据可用性」是两种语义，本模块只依赖后者**：

- 凭据可用性 = ``key.provisioned_key_version``（Controller 在真实网关确认过的版本）；
- 对账进度 = ``policy.status``，**仅**用于在凭据不可用时区分「秒级自愈的瞬态」与
  「需要人介入的永久缺陷」。

把二者混用会造成两类真实故障：pending 只是进度未完成（如管理员改了 rpm_limit），此刻网关侧
旧版本仍完全可用，按不可用处理会在对账窗口内打死并发 Run；反过来，用 desired 版本派生则会在
轮换落库后、对账完成前拿到网关上还不存在的 Key。

解析只读平台数据库、不调用网关管理接口：LiteLLM 管理面故障不会波及已 provisioned 的租户。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from agent_platform.platform.model_gateway.credentials import derive_tenant_gateway_key
from agent_platform.platform.model_gateway.entities import (
    ModelGatewayPolicyStatus,
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayCredentialNotReady,
    ModelGatewayCredentialUnavailable,
)


class TenantGatewayStateReader(Protocol):
    async def get_policy(self, tenant_id: UUID) -> TenantModelGatewayPolicy | None: ...

    async def get_key(self, tenant_id: UUID) -> TenantModelGatewayKey | None: ...


class TenantGatewayCredentialResolver:
    def __init__(
        self,
        *,
        reader: TenantGatewayStateReader,
        key_secret: SecretStr,
    ) -> None:
        self._reader = reader
        self._key_secret = key_secret

    async def resolve(self, *, tenant_id: UUID, alias: str) -> SecretStr:
        policy = await self._reader.get_policy(tenant_id)
        # 以下三项都是管理员可见的配置事实，重投不会改变结果 → 永久失败。
        if policy is None:
            raise ModelGatewayCredentialUnavailable("model_gateway_policy_not_provisioned")
        if not policy.enabled:
            # 撤销是管理员的明确动作，立即拒绝，不等网关侧阻断完成。
            raise ModelGatewayCredentialUnavailable("model_gateway_disabled")
        if alias not in policy.allowed_aliases:
            # 租户策略是 alias 授权的真相源；发布定义可能早于策略收紧。
            raise ModelGatewayCredentialUnavailable("model_gateway_alias_not_allowed")
        key = await self._reader.get_key(tenant_id)
        if key is None or key.provisioned_key_version is None:
            if policy.status is ModelGatewayPolicyStatus.ERROR:
                # 对账已确定失败且网关侧无可用 Key：重投永远不会好，需要人介入。
                raise ModelGatewayCredentialUnavailable("model_gateway_provisioning_failed")
            # 对账进行中：Controller 秒级收敛，交队列重投而不是判死这个 Run。
            raise ModelGatewayCredentialNotReady("model_gateway_provisioning_in_progress")
        # 用 observed 而非 desired 派生：轮换落库后、对账完成前，网关上只有旧版本存在。
        return derive_tenant_gateway_key(
            secret=self._key_secret,
            tenant_id=tenant_id,
            key_version=key.provisioned_key_version,
        )
