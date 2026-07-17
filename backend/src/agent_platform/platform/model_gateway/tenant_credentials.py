"""按租户解析可归因的模型网关凭据。

Worker 每次运行都用本模块把「租户 desired 策略 + 已对账的 Key 版本」翻译成该租户专属的
虚拟 Key，取代此前应用级共享 Key。任何不确定状态（无策略、已撤销、尚未对账、Key 未签发、
alias 越权）一律失败关闭，绝不回退共享凭据——回退等于让调用重新变得不可归因。

解析只读平台数据库、不调用网关管理接口：LiteLLM 管理面故障不会波及存量 active 租户的推理。
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
from agent_platform.platform.model_gateway.errors import ModelGatewayCredentialUnavailable


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
        if policy is None:
            raise ModelGatewayCredentialUnavailable("model_gateway_policy_not_provisioned")
        if not policy.enabled:
            raise ModelGatewayCredentialUnavailable("model_gateway_disabled")
        if policy.status is not ModelGatewayPolicyStatus.ACTIVE:
            # pending/error：网关侧未必存在可用 Key，乐观放行会把配置缺陷变成运行期 401。
            raise ModelGatewayCredentialUnavailable("model_gateway_not_active")
        if alias not in policy.allowed_aliases:
            # 租户策略是 alias 授权的真相源；发布定义可能早于策略收紧。
            raise ModelGatewayCredentialUnavailable("model_gateway_alias_not_allowed")
        key = await self._reader.get_key(tenant_id)
        if key is None:
            raise ModelGatewayCredentialUnavailable("model_gateway_key_not_provisioned")
        return derive_tenant_gateway_key(
            secret=self._key_secret,
            tenant_id=tenant_id,
            key_version=key.key_version,
        )
