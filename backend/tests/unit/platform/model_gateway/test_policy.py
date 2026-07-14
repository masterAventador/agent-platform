from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_platform.platform.model_gateway.entities import (
    MAX_BUDGET_MICROUSD,
    MAX_SIGNED_INT32,
    ModelGatewayBudgetPeriod,
    ModelGatewayPolicyStatus,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import InvalidModelGatewayPolicy


def _create_policy(**overrides: object) -> TenantModelGatewayPolicy:
    values: dict[str, object] = {
        "tenant_id": uuid4(),
        "enabled": True,
        "allowed_aliases": {"general-purpose"},
        "budget_microusd": 1_000_000,
        "budget_period": "monthly",
        "rpm_limit": 60,
        "tpm_limit": 120_000,
        "max_parallel_requests": 4,
        "revision": 1,
        "updated_by": uuid4(),
        "now": datetime(2026, 7, 14, tzinfo=UTC),
    }
    values.update(overrides)
    return TenantModelGatewayPolicy.create_desired(**values)  # type: ignore[arg-type]


def test_desired_policy_is_provider_neutral_and_pending() -> None:
    policy = _create_policy()

    assert policy.allowed_aliases == frozenset({"general-purpose"})
    assert policy.budget_period is ModelGatewayBudgetPeriod.MONTHLY
    assert policy.status is ModelGatewayPolicyStatus.PENDING
    assert policy.created_at == policy.updated_at


def test_json_safe_maximum_budget_is_accepted() -> None:
    assert _create_policy(budget_microusd=MAX_BUDGET_MICROUSD).budget_microusd == (
        MAX_BUDGET_MICROUSD
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowed_aliases", set()),
        ("allowed_aliases", {"provider-model"}),
        ("enabled", 1),
        ("budget_microusd", 0),
        ("budget_microusd", -1),
        ("budget_microusd", True),
        ("budget_microusd", 1.5),
        ("budget_microusd", MAX_BUDGET_MICROUSD + 1),
        ("budget_period", "daily"),
        ("rpm_limit", 0),
        ("rpm_limit", True),
        ("rpm_limit", 1.5),
        ("tpm_limit", -1),
        ("max_parallel_requests", 0),
        ("revision", 0),
        ("rpm_limit", MAX_SIGNED_INT32 + 1),
        ("tpm_limit", MAX_SIGNED_INT32 + 1),
        ("max_parallel_requests", MAX_SIGNED_INT32 + 1),
        ("revision", MAX_SIGNED_INT32 + 1),
    ],
)
def test_invalid_desired_policy_is_rejected(field: str, value: object) -> None:
    with pytest.raises(InvalidModelGatewayPolicy) as captured:
        _create_policy(**{field: value})

    assert captured.value.code == "invalid_model_gateway_policy"


def test_revising_policy_preserves_created_at_and_advances_revision() -> None:
    current = _create_policy()
    updated_at = datetime(2026, 8, 1, tzinfo=UTC)

    revised = current.revise_desired(
        enabled=False,
        allowed_aliases={"general-purpose"},
        budget_microusd=2_000_000,
        budget_period="monthly",
        rpm_limit=30,
        tpm_limit=80_000,
        max_parallel_requests=2,
        updated_by=uuid4(),
        now=updated_at,
    )

    assert revised.revision == 2
    assert revised.status is ModelGatewayPolicyStatus.PENDING
    assert revised.created_at == current.created_at
    assert revised.updated_at == updated_at


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowed_aliases", {"unsupported"}),
        ("rpm_limit", MAX_SIGNED_INT32 + 1),
        ("status", "unknown"),
        ("status", "disabled"),
        ("enabled", False),
        ("created_at", datetime(2026, 7, 14)),
        ("updated_at", datetime(2026, 7, 13, tzinfo=UTC)),
    ],
)
def test_restore_rejects_corrupt_persisted_policy(field: str, value: object) -> None:
    values: dict[str, object] = {
        "tenant_id": uuid4(),
        "enabled": True,
        "allowed_aliases": {"general-purpose"},
        "budget_microusd": 1_000_000,
        "budget_period": "monthly",
        "rpm_limit": 60,
        "tpm_limit": 120_000,
        "max_parallel_requests": 4,
        "revision": 1,
        "status": "active",
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 15, tzinfo=UTC),
        "updated_by": uuid4(),
    }
    values[field] = value

    with pytest.raises(InvalidModelGatewayPolicy):
        TenantModelGatewayPolicy.restore(**values)  # type: ignore[arg-type]
