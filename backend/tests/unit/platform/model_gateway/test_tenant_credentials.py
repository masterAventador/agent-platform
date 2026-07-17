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
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayCredentialNotReady,
    ModelGatewayCredentialUnavailable,
)
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


def _key(
    tenant_id: UUID,
    *,
    version: int = 1,
    provisioned: int | None = -1,
    retired: int | None = None,
) -> TenantModelGatewayKey:
    return TenantModelGatewayKey.restore(
        tenant_id=tenant_id,
        key_version=version,
        retired_key_version=retired,
        # 默认 provisioned 与 desired 同版本；传 None 表示网关侧尚无可用 Key
        provisioned_key_version=version if provisioned == -1 else provisioned,
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


@pytest.mark.asyncio
async def test_a_pending_policy_keeps_using_the_already_provisioned_key() -> None:
    """S2 根因：对账进度 != 凭据可用性。

    管理员把 rpm_limit 60 改成 61 → revision+1、status=pending。网关侧 v1 依然真实
    可用，此刻启动的 Run 必须照常跑，不能因为「对账进度未完成」被判死。
    """
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(tenant_id, status=ModelGatewayPolicyStatus.PENDING),
        key=_key(tenant_id, version=1, provisioned=1),
    )

    credential = await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert credential.get_secret_value() == derive_tenant_gateway_key(
        secret=SECRET, tenant_id=tenant_id, key_version=1
    ).get_secret_value()


@pytest.mark.asyncio
async def test_a_rotation_in_flight_keeps_using_the_last_provisioned_version() -> None:
    """轮换已落库但尚未对账：网关侧只有 v1 存在，必须用 v1 而不是尚不存在的 v2。"""
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(tenant_id, status=ModelGatewayPolicyStatus.PENDING),
        key=_key(tenant_id, version=2, provisioned=1, retired=1),
    )

    credential = await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert credential.get_secret_value() == derive_tenant_gateway_key(
        secret=SECRET, tenant_id=tenant_id, key_version=1
    ).get_secret_value()


@pytest.mark.asyncio
async def test_a_failed_rotation_still_serves_the_working_previous_key() -> None:
    """M2：轮换永久失败时网关侧 v1 仍完全可用，租户绝不能因此全线不可用。"""
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(tenant_id, status=ModelGatewayPolicyStatus.ERROR),
        key=_key(tenant_id, version=2, provisioned=1, retired=1),
    )

    credential = await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert credential.get_secret_value() == derive_tenant_gateway_key(
        secret=SECRET, tenant_id=tenant_id, key_version=1
    ).get_secret_value()


@pytest.mark.asyncio
async def test_an_unprovisioned_key_is_transient_because_the_controller_self_heals() -> None:
    """尚未对账 = 秒级自愈的瞬态，必须交队列重投，不得判为永久定义错误。"""
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(tenant_id, status=ModelGatewayPolicyStatus.PENDING),
        key=_key(tenant_id, provisioned=None),
    )

    with pytest.raises(ModelGatewayCredentialNotReady) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert captured.value.code == "model_gateway_provisioning_in_progress"


@pytest.mark.asyncio
async def test_a_missing_key_row_before_the_first_reconcile_is_transient() -> None:
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(tenant_id, status=ModelGatewayPolicyStatus.PENDING), key=None
    )

    with pytest.raises(ModelGatewayCredentialNotReady):
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")


@pytest.mark.asyncio
async def test_a_definitively_failed_provisioning_is_permanent_not_transient() -> None:
    """对账确定失败且网关侧无可用 Key：重投永远不会好，必须永久失败。"""
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(tenant_id, status=ModelGatewayPolicyStatus.ERROR),
        key=_key(tenant_id, provisioned=None),
    )

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert not isinstance(captured.value, ModelGatewayCredentialNotReady)
    assert captured.value.code == "model_gateway_provisioning_failed"


@pytest.mark.asyncio
async def test_a_revoked_policy_is_rejected_even_while_a_key_is_still_provisioned() -> None:
    """撤销是管理员的明确动作：即使网关侧 Key 尚未被阻断也必须立即拒绝。"""
    tenant_id = uuid4()
    resolver = _resolver(
        policy=_policy(
            tenant_id, enabled=False, status=ModelGatewayPolicyStatus.PENDING
        ),
        key=_key(tenant_id, provisioned=1),
    )

    with pytest.raises(ModelGatewayCredentialUnavailable) as captured:
        await resolver.resolve(tenant_id=tenant_id, alias="general-purpose")

    assert not isinstance(captured.value, ModelGatewayCredentialNotReady)
    assert captured.value.code == "model_gateway_disabled"


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
