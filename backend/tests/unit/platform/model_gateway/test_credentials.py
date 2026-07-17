"""租户网关虚拟 Key 的派生契约（C16 阶段一）。

Key 明文不落库、不出接口：由服务端密钥 + tenant_id + key_version 确定性派生，
Controller 与 Worker 共用同一函数，保证跨进程一致。
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from agent_platform.platform.model_gateway.credentials import (
    ModelGatewayKeySecretNotConfiguredError,
    derive_tenant_gateway_key,
    tenant_gateway_key_alias,
)

_SECRET = SecretStr("model-gateway-key-secret-for-unit-tests")
_TENANT = UUID("11111111-1111-1111-1111-111111111111")


def test_derived_key_matches_the_litellm_raw_key_contract() -> None:
    key = derive_tenant_gateway_key(secret=_SECRET, tenant_id=_TENANT, key_version=1)

    assert re.fullmatch(r"sk-[A-Za-z0-9_-]{32,}", key.get_secret_value())
    assert len(set(key.get_secret_value().removeprefix("sk-"))) >= 12


def test_derivation_is_deterministic_across_processes() -> None:
    first = derive_tenant_gateway_key(secret=_SECRET, tenant_id=_TENANT, key_version=3)
    second = derive_tenant_gateway_key(secret=_SECRET, tenant_id=_TENANT, key_version=3)

    assert first.get_secret_value() == second.get_secret_value()


def test_each_tenant_version_and_secret_derives_a_distinct_key() -> None:
    baseline = derive_tenant_gateway_key(
        secret=_SECRET, tenant_id=_TENANT, key_version=1
    ).get_secret_value()
    other_tenant = derive_tenant_gateway_key(
        secret=_SECRET, tenant_id=uuid4(), key_version=1
    ).get_secret_value()
    other_version = derive_tenant_gateway_key(
        secret=_SECRET, tenant_id=_TENANT, key_version=2
    ).get_secret_value()
    other_secret = derive_tenant_gateway_key(
        secret=SecretStr("a-different-secret"), tenant_id=_TENANT, key_version=1
    ).get_secret_value()

    assert len({baseline, other_tenant, other_version, other_secret}) == 4


def test_derivation_rejects_an_empty_secret_and_invalid_versions() -> None:
    with pytest.raises(ModelGatewayKeySecretNotConfiguredError):
        derive_tenant_gateway_key(secret=SecretStr(""), tenant_id=_TENANT, key_version=1)
    for version in (0, -1, True):
        with pytest.raises(ValueError):
            derive_tenant_gateway_key(secret=_SECRET, tenant_id=_TENANT, key_version=version)


def test_derived_key_never_appears_in_repr_or_str() -> None:
    key = derive_tenant_gateway_key(secret=_SECRET, tenant_id=_TENANT, key_version=1)

    assert key.get_secret_value() not in repr(key)
    assert key.get_secret_value() not in str(key)


def test_key_alias_is_tenant_attributable_and_matches_the_admin_contract() -> None:
    alias = tenant_gateway_key_alias(tenant_id=_TENANT, key_version=7)

    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,125}[a-z0-9]", alias)
    assert str(_TENANT) in alias
    assert alias != tenant_gateway_key_alias(tenant_id=_TENANT, key_version=8)

