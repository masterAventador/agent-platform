"""Worker 侧租户网关凭据解析契约（C16 阶段一）。

Worker 不再使用应用级共享 Key：每次运行按租户解析可归因凭据。策略缺失、未启用、
未对账、Key 未签发或 alias 越权时一律失败关闭——绝不回退共享 Key。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from agent_platform.platform.model_gateway.credentials import derive_tenant_gateway_key
from agent_platform.platform.model_gateway.entities import (
    ModelGatewayPolicyStatus,
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import ModelGatewayCredentialUnavailable
from agent_platform.platform.model_gateway.tenant_credentials import (
    TenantGatewayCredentialResolver,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)
SECRET = SecretStr("model-gateway-key-secret-for-worker-tests")


def _policy(
    tenant_id: UUID,
    *,
    enabled: bool = True,
    status: ModelGatewayPolicyStatus = ModelGatewayPolicyStatus.ACTIVE,
    aliases: set[str] | None = None,
) -> TenantModelGatewayPolicy:
    return TenantModelGatewayPolicy.restore(
        tenant_id=tenant_id,
        enabled=enabled,
        allowed_aliases=aliases or {"general-purpose"},
        budget_microusd=1_000_000,
        budget_period="monthly",
        rpm_limit=60,
        tpm_limit=100_000,
        max_parallel_requests=4,
        revision=1,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        updated_by=uuid4(),
    )


def _key(tenant_id: UUID, *, version: int = 1) -> TenantModelGatewayKey:
    return TenantModelGatewayKey.restore(
        tenant_id=tenant_id,
        key_version=version,
        retired_key_version=None,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeReader:
    def __init__(
        self,
        *,
        policy: TenantModelGatewayPolicy | None,
        key: TenantModelGatewayKey | None,
    ) -> None:
        self._policy = policy
        self._key = key

    async def get_policy(self, tenant_id: UUID) -> TenantModelGatewayPolicy | None:
        return self._policy

    async def get_key(self, tenant_id: UUID) -> TenantModelGatewayKey | None:
        return self._key


def _resolver(
    *,
    policy: TenantModelGatewayPolicy | None,
    key: TenantModelGatewayKey | None,
) -> TenantGatewayCredentialResolver:
    return TenantGatewayCredentialResolver(
        reader=FakeReader(policy=policy, key=key), key_secret=SECRET
    )


@pytest.mark.asyncio
async def test_an_active_policy_resolves_the_attributable_tenant_key() -> None:
    tenant_id = uuid4()
    resolver = _resolver(policy=_policy(tenant_id), key=_key(tenant_id, version=3))

    credential = await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert credential.get_secret_value() == derive_tenant_gateway_key(
        secret=SECRET, tenant_id=tenant_id, key_version=3
    ).get_secret_value()


@pytest.mark.asyncio
async def test_each_tenant_resolves_a_distinct_key() -> None:
    first, second = uuid4(), uuid4()
    first_credential = await _resolver(
        policy=_policy(first), key=_key(first)
    ).resolve(tenant_id=first, alias="general-purpose")
    second_credential = await _resolver(
        policy=_policy(second), key=_key(second)
    ).resolve(tenant_id=second, alias="general-purpose")

    assert first_credential.get_secret_value() != second_credential.get_secret_value()


@pytest.mark.asyncio
async def test_a_tenant_without_a_policy_fails_closed_and_never_falls_back() -> None:
    tenant_id = uuid4()
    resolver = _resolver(policy=None, key=None)

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert captured.value.code == "model_gateway_policy_not_provisioned"


@pytest.mark.asyncio
async def test_a_disabled_policy_is_rejected_immediately() -> None:
    """撤销（enabled=false）后新的模型调用必须立即被拒。"""
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(
            tenant_id, enabled=False, status=ModelGatewayPolicyStatus.DISABLED
        ),
        key=_key(tenant_id),
    )

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert captured.value.code == "model_gateway_disabled"


@pytest.mark.parametrize(
    "status",
    [
        ModelGatewayPolicyStatus.PENDING,
        ModelGatewayPolicyStatus.ERROR,
    ],
)
@pytest.mark.asyncio
async def test_a_policy_that_is_not_reconciled_yet_is_rejected(
    status: ModelGatewayPolicyStatus,
) -> None:
    """尚未对账/对账失败的策略：网关侧未必存在可用 Key，绝不能乐观放行。"""
    tenant_id = uuid4()
    resolver = _resolver(policy=_policy(tenant_id, status=status), key=_key(tenant_id))

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert captured.value.code == "model_gateway_not_active"


@pytest.mark.asyncio
async def test_an_active_policy_without_a_provisioned_key_fails_closed() -> None:
    tenant_id = uuid4()
    resolver = _resolver(policy=_policy(tenant_id), key=None)

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert captured.value.code == "model_gateway_key_not_provisioned"


@pytest.mark.asyncio
async def test_an_alias_outside_the_tenant_policy_is_rejected() -> None:
    """租户策略是 alias 授权的真相源：Worker 必须重复校验，不能只信发布定义。"""
    tenant_id = uuid4()
    resolver = _resolver(policy=_policy(tenant_id), key=_key(tenant_id))

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="unlisted-alias")

    assert captured.value.code == "model_gateway_alias_not_allowed"


@pytest.mark.asyncio
async def test_credential_errors_never_leak_key_material() -> None:
    tenant_id = uuid4()
    resolver = _resolver(policy=None, key=None)

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    rendered = f"{captured.value!r}\n{captured.value}"
    assert SECRET.get_secret_value() not in rendered
