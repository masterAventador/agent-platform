"""Provisioning Controller 的对账决策契约（C16 阶段一）。

Reconciler 只做决策，不持有事务与 LiteLLM 细节：命令认领/提交由 store 负责，
真实网关调用由 provisioner 端口负责。本层验证状态推进、错误分类、退避上限与
「结果不确定绝不自动重放」。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.model_gateway.entities import (
    ModelGatewayPolicyStatus,
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayProvisioningOutcomeUnknown,
    ModelGatewayProvisioningPermanent,
    ModelGatewayProvisioningTransient,
)
from agent_platform.platform.model_gateway.ports import (
    ClaimedProvisioningCommand,
    ModelGatewayProvisioningAction,
    ProvisioningCommandStatus,
    ReconcileOutcome,
)
from agent_platform.platform.model_gateway.reconciler import (
    DEFAULT_MAX_PROVISIONING_ATTEMPTS,
    ModelGatewayReconciler,
)

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _policy(*, tenant_id: UUID, enabled: bool = True) -> TenantModelGatewayPolicy:
    return TenantModelGatewayPolicy.create_desired(
        tenant_id=tenant_id,
        enabled=enabled,
        allowed_aliases={"general-purpose"},
        budget_microusd=1_000_000,
        budget_period="monthly",
        rpm_limit=60,
        tpm_limit=100_000,
        max_parallel_requests=4,
        revision=1,
        updated_by=uuid4(),
        now=_NOW,
    )


def _key(*, tenant_id: UUID, retired: int | None = None) -> TenantModelGatewayKey:
    return TenantModelGatewayKey.restore(
        tenant_id=tenant_id,
        key_version=2 if retired else 1,
        retired_key_version=retired,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _claim(
    *,
    policy: TenantModelGatewayPolicy,
    key: TenantModelGatewayKey,
    attempts: int = 0,
    action: ModelGatewayProvisioningAction = ModelGatewayProvisioningAction.RECONCILE,
) -> ClaimedProvisioningCommand:
    return ClaimedProvisioningCommand(
        command_id=uuid4(),
        tenant_id=policy.tenant_id,
        desired_revision=policy.revision,
        action=action,
        attempts=attempts,
        policy=policy,
        key=key,
    )


@dataclass
class FakeProvisioner:
    """记录真实网关侧调用；可注入受控异常。"""

    error: Exception | None = None
    enabled_calls: list[tuple[UUID, int]] = None  # type: ignore[assignment]
    disabled_calls: list[tuple[UUID, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.enabled_calls = []
        self.disabled_calls = []

    async def apply_enabled(
        self, *, policy: TenantModelGatewayPolicy, key: TenantModelGatewayKey
    ) -> None:
        if self.error is not None:
            raise self.error
        self.enabled_calls.append((policy.tenant_id, key.key_version))

    async def apply_disabled(
        self, *, policy: TenantModelGatewayPolicy, key: TenantModelGatewayKey
    ) -> None:
        if self.error is not None:
            raise self.error
        self.disabled_calls.append((policy.tenant_id, key.key_version))


class FakeStore:
    """单条命令的 store 替身；语义与真实仓储的 process_next 一致。"""

    def __init__(self, claim: ClaimedProvisioningCommand | None) -> None:
        self._claim = claim
        self.outcome: ReconcileOutcome | None = None
        self.pruned_before: datetime | None = None

    async def process_next(
        self,
        handler: Callable[[ClaimedProvisioningCommand], Awaitable[ReconcileOutcome]],
        *,
        now: datetime,
    ) -> bool:
        if self._claim is None:
            return False
        self.outcome = await handler(self._claim)
        return True

    async def prune_settled(self, *, older_than: datetime, limit: int) -> int:
        self.pruned_before = older_than
        return 0


async def _run(
    *,
    claim: ClaimedProvisioningCommand,
    provisioner: FakeProvisioner,
) -> ReconcileOutcome:
    store = FakeStore(claim)
    reconciler = ModelGatewayReconciler(store=store, provisioner=provisioner)
    processed = await reconciler.reconcile_once(now=_NOW)
    assert processed is True
    assert store.outcome is not None
    return store.outcome


@pytest.mark.asyncio
async def test_enabled_policy_reconciles_to_active_and_clears_retirement() -> None:
    tenant_id = uuid4()
    provisioner = FakeProvisioner()

    outcome = await _run(
        claim=_claim(policy=_policy(tenant_id=tenant_id), key=_key(tenant_id=tenant_id)),
        provisioner=provisioner,
    )

    assert outcome.command_status is ProvisioningCommandStatus.COMPLETED
    assert outcome.policy_status is ModelGatewayPolicyStatus.ACTIVE
    assert outcome.clear_key_retirement is True
    assert outcome.error_code is None
    assert provisioner.enabled_calls == [(tenant_id, 1)]
    assert provisioner.disabled_calls == []


@pytest.mark.asyncio
async def test_disabled_policy_reconciles_to_disabled_and_blocks_the_key() -> None:
    tenant_id = uuid4()
    provisioner = FakeProvisioner()

    outcome = await _run(
        claim=_claim(
            policy=_policy(tenant_id=tenant_id, enabled=False),
            key=_key(tenant_id=tenant_id),
        ),
        provisioner=provisioner,
    )

    assert outcome.command_status is ProvisioningCommandStatus.COMPLETED
    assert outcome.policy_status is ModelGatewayPolicyStatus.DISABLED
    assert provisioner.disabled_calls == [(tenant_id, 1)]
    assert provisioner.enabled_calls == []


@pytest.mark.asyncio
async def test_reconciling_the_same_revision_twice_converges_to_the_same_outcome() -> None:
    tenant_id = uuid4()
    provisioner = FakeProvisioner()
    claim = _claim(policy=_policy(tenant_id=tenant_id), key=_key(tenant_id=tenant_id))

    first = await _run(claim=claim, provisioner=provisioner)
    second = await _run(claim=claim, provisioner=provisioner)

    assert first == second
    assert provisioner.enabled_calls == [(tenant_id, 1), (tenant_id, 1)]


@pytest.mark.asyncio
async def test_transient_failures_retry_with_bounded_exponential_backoff() -> None:
    tenant_id = uuid4()
    provisioner = FakeProvisioner(error=ModelGatewayProvisioningTransient())

    first = await _run(
        claim=_claim(
            policy=_policy(tenant_id=tenant_id), key=_key(tenant_id=tenant_id), attempts=0
        ),
        provisioner=provisioner,
    )
    later = await _run(
        claim=_claim(
            policy=_policy(tenant_id=tenant_id), key=_key(tenant_id=tenant_id), attempts=3
        ),
        provisioner=provisioner,
    )

    assert first.command_status is ProvisioningCommandStatus.PENDING
    # 瞬态失败不得推进策略状态：租户仍应看到 pending 而不是 error
    assert first.policy_status is None
    assert first.error_code == "provisioning_transient"
    assert first.next_attempt_at is not None and first.next_attempt_at > _NOW
    assert later.next_attempt_at is not None
    assert later.next_attempt_at > first.next_attempt_at
    assert later.next_attempt_at <= _NOW + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_transient_retries_are_bounded_and_end_in_a_controlled_error() -> None:
    tenant_id = uuid4()
    provisioner = FakeProvisioner(error=ModelGatewayProvisioningTransient())

    outcome = await _run(
        claim=_claim(
            policy=_policy(tenant_id=tenant_id),
            key=_key(tenant_id=tenant_id),
            attempts=DEFAULT_MAX_PROVISIONING_ATTEMPTS - 1,
        ),
        provisioner=provisioner,
    )

    assert outcome.command_status is ProvisioningCommandStatus.FAILED
    assert outcome.policy_status is ModelGatewayPolicyStatus.ERROR
    assert outcome.error_code == "provisioning_retry_exhausted"
    assert outcome.next_attempt_at is None


@pytest.mark.asyncio
async def test_permanent_failures_stop_burning_retries_immediately() -> None:
    tenant_id = uuid4()
    provisioner = FakeProvisioner(
        error=ModelGatewayProvisioningPermanent("provisioning_rejected")
    )

    outcome = await _run(
        claim=_claim(policy=_policy(tenant_id=tenant_id), key=_key(tenant_id=tenant_id)),
        provisioner=provisioner,
    )

    assert outcome.command_status is ProvisioningCommandStatus.FAILED
    assert outcome.policy_status is ModelGatewayPolicyStatus.ERROR
    assert outcome.error_code == "provisioning_rejected"


@pytest.mark.asyncio
async def test_unknown_outcomes_are_never_auto_replayed() -> None:
    """结果不确定时禁止自动重放（同 Tool Gateway 的 tool_execution_uncertain 哲学）。"""
    tenant_id = uuid4()
    provisioner = FakeProvisioner(error=ModelGatewayProvisioningOutcomeUnknown())

    outcome = await _run(
        claim=_claim(policy=_policy(tenant_id=tenant_id), key=_key(tenant_id=tenant_id)),
        provisioner=provisioner,
    )

    assert outcome.command_status is ProvisioningCommandStatus.FAILED
    assert outcome.policy_status is ModelGatewayPolicyStatus.ERROR
    assert outcome.error_code == "provisioning_outcome_unknown"
    assert outcome.next_attempt_at is None
    assert outcome.clear_key_retirement is False


@pytest.mark.asyncio
async def test_rotation_reconciles_the_new_version_and_clears_the_retirement() -> None:
    """轮换后的对账必须在成功后清空 retired 摘要，否则旧版本 Key 永远无人回收。"""
    tenant_id = uuid4()
    provisioner = FakeProvisioner()

    outcome = await _run(
        claim=_claim(
            policy=_policy(tenant_id=tenant_id),
            key=_key(tenant_id=tenant_id, retired=1),
        ),
        provisioner=provisioner,
    )

    assert outcome.command_status is ProvisioningCommandStatus.COMPLETED
    assert outcome.policy_status is ModelGatewayPolicyStatus.ACTIVE
    assert outcome.clear_key_retirement is True


@pytest.mark.asyncio
async def test_reconcile_once_reports_an_empty_outbox() -> None:
    reconciler = ModelGatewayReconciler(store=FakeStore(None), provisioner=FakeProvisioner())

    assert await reconciler.reconcile_once(now=_NOW) is False


@pytest.mark.asyncio
async def test_settled_commands_are_pruned_with_a_bounded_retention() -> None:
    store = FakeStore(None)
    reconciler = ModelGatewayReconciler(store=store, provisioner=FakeProvisioner())

    await reconciler.prune_settled_commands(now=_NOW, retention=timedelta(days=7), limit=100)

    assert store.pruned_before == _NOW - timedelta(days=7)
