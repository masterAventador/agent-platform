"""把租户 desired 策略对账到真实 LiteLLM 的公开管理接口。

零侵入边界：只调用 ``LiteLLMAdminClient`` 暴露的官方 HTTP 路由，不触碰 LiteLLM 源码、
内部类型或其数据库。上游错误在此处一次性映射为平台端口语义，业务层不感知 LiteLLM。

派生 Key 明文只存在于本进程内存与发往 LiteLLM 的请求体中：既不返回给调用者，
也不进入任何平台错误、日志或持久化字段。
"""

from __future__ import annotations

from pydantic import SecretStr

from agent_platform.infrastructure.llm.admin import (
    LiteLLMAdminClient,
    LiteLLMAdminConfigurationError,
    LiteLLMAdminError,
    LiteLLMAdminOutcomeUnknown,
    LiteLLMAdminValidationError,
    VirtualKeyRecord,
)
from agent_platform.platform.model_gateway.credentials import (
    derive_tenant_gateway_key,
    tenant_gateway_key_alias,
    tenant_gateway_key_digest,
)
from agent_platform.platform.model_gateway.entities import (
    ModelGatewayBudgetPeriod,
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayProvisioningOutcomeUnknown,
    ModelGatewayProvisioningPermanent,
    ModelGatewayProvisioningTransient,
)

# 租户 Key 只允许 OpenAI-compatible 推理与模型发现路由，禁止触达任何管理路由。
TENANT_ALLOWED_ROUTES = (
    "/chat/completions",
    "/models",
    "/v1/chat/completions",
    "/v1/models",
)
_BUDGET_DURATIONS = {ModelGatewayBudgetPeriod.MONTHLY: "1mo"}
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})


class LiteLLMModelGatewayProvisioner:
    def __init__(
        self,
        *,
        admin: LiteLLMAdminClient,
        key_secret: SecretStr,
    ) -> None:
        self._admin = admin
        self._key_secret = key_secret

    async def apply_enabled(
        self,
        *,
        policy: TenantModelGatewayPolicy,
        key: TenantModelGatewayKey,
    ) -> None:
        models = sorted(policy.allowed_aliases)
        alias = tenant_gateway_key_alias(
            tenant_id=key.tenant_id, key_version=key.key_version
        )
        try:
            await self._admin.ensure_tenant_aggregate(
                policy.tenant_id,
                max_budget_microusd=policy.budget_microusd,
                budget_duration=_BUDGET_DURATIONS[policy.budget_period],
                rpm_limit=policy.rpm_limit,
                tpm_limit=policy.tpm_limit,
                max_parallel_requests=policy.max_parallel_requests,
                models=models,
            )
            # 重放安全：generate_blocked_key 只在 Key 尚未存在时调用。已存在的 Key 一定
            # 处于 unblocked（本方法的终态），与 generate 期望的 blocked 终态不符，
            # 直接重放会被上游的 verify 判为冲突。
            digest = self._digest(key, key.key_version)
            existing = await self._admin.get_key(digest)
            if existing is None:
                # 先以 blocked 生成再解除阻断：生成阶段的半成功不会留下可用凭据。
                await self._admin.generate_blocked_key(
                    policy.tenant_id,
                    raw_key=self._derive(key, key.key_version),
                    key_alias=alias,
                    models=models,
                    allowed_routes=TENANT_ALLOWED_ROUTES,
                )
            elif not _scope_matches(
                existing,
                tenant_id=policy.tenant_id,
                key_alias=alias,
                models=models,
            ):
                # 网关侧已有同摘要 Key 但范围与 desired 不符：可能是人为改动或跨租户
                # 冲突。放宽复用等于接受未知授权范围，一律失败关闭交人工处置。
                raise ModelGatewayProvisioningPermanent("provisioning_key_scope_conflict")
            await self._admin.unblock_key(policy.tenant_id, digest)
            await self._retire(key)
        except (
            LiteLLMAdminOutcomeUnknown,
            LiteLLMAdminError,
            LiteLLMAdminValidationError,
            LiteLLMAdminConfigurationError,
        ) as error:
            _raise_platform_error(error)

    async def apply_disabled(
        self,
        *,
        policy: TenantModelGatewayPolicy,
        key: TenantModelGatewayKey,
    ) -> None:
        try:
            await self._admin.block_key(
                policy.tenant_id, self._digest(key, key.key_version)
            )
            await self._retire(key)
        except (
            LiteLLMAdminOutcomeUnknown,
            LiteLLMAdminError,
            LiteLLMAdminValidationError,
            LiteLLMAdminConfigurationError,
        ) as error:
            _raise_platform_error(error)

    async def _retire(self, key: TenantModelGatewayKey) -> None:
        if key.retired_key_version is not None:
            await self._admin.delete_key(
                key.tenant_id, self._digest(key, key.retired_key_version)
            )

    def _derive(self, key: TenantModelGatewayKey, version: int) -> SecretStr:
        return derive_tenant_gateway_key(
            secret=self._key_secret,
            tenant_id=key.tenant_id,
            key_version=version,
        )

    def _digest(self, key: TenantModelGatewayKey, version: int) -> str:
        return tenant_gateway_key_digest(self._derive(key, version))


def _scope_matches(
    record: VirtualKeyRecord,
    *,
    tenant_id: object,
    key_alias: str,
    models: list[str],
) -> bool:
    return (
        record.tenant_id == str(tenant_id)
        and record.key_alias == key_alias
        and list(record.models) == models
        and set(record.allowed_routes) == set(TENANT_ALLOWED_ROUTES)
    )


def _raise_platform_error(error: Exception) -> None:
    if isinstance(error, LiteLLMAdminOutcomeUnknown):
        raise ModelGatewayProvisioningOutcomeUnknown() from None
    if isinstance(error, LiteLLMAdminError):
        status = error.status_code
        if status is None or status >= 500 or status in _RETRYABLE_STATUSES:
            raise ModelGatewayProvisioningTransient() from None
        raise ModelGatewayProvisioningPermanent() from None
    # 输入或配置无效：重试永远得到同一结果。
    raise ModelGatewayProvisioningPermanent() from None
