"""Drive the real provisioning reconcile path against a real LiteLLM container.

Stub-only proof is not enough: C04 (fake COS relaxed the real SDK contract) and C07
(stub field names diverged from real RAGFlow) both shipped green and broke on first
real contact. This probe runs the production LiteLLMModelGatewayProvisioner against a
real LiteLLM v1.86.2 admin API and asserts the tenant aggregate, key issuance,
idempotent replay, rotation retirement and revocation all round-trip for real.

`inference` mode closes the gap that let S1 ship: asserting the admin API records a key
proves nothing about whether the *derived* key can actually authenticate an inference
call. It provisions a tenant the same way the Controller does, then drives a real
/chat/completions with the derived credential and asserts HTTP 200 — and asserts that a
never-provisioned tenant's derived key is rejected, which is exactly what every Demo run
hit when the seed faked a terminal state without ever enqueuing a reconcile command.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import SecretStr

from agent_platform.infrastructure.llm.admin import LiteLLMAdminClient
from agent_platform.infrastructure.llm.provisioner import LiteLLMModelGatewayProvisioner
from agent_platform.platform.model_gateway.credentials import (
    derive_tenant_gateway_key,
    tenant_gateway_key_digest,
)
from agent_platform.platform.model_gateway.entities import (
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)

NOW = datetime.now(UTC)
KEY_SECRET = SecretStr(os.environ["MODEL_GATEWAY_KEY_SECRET"])
TENANT_ID = uuid4()


def _policy(*, enabled: bool = True) -> TenantModelGatewayPolicy:
    return TenantModelGatewayPolicy.create_desired(
        tenant_id=TENANT_ID,
        enabled=enabled,
        allowed_aliases={"general-purpose"},
        budget_microusd=25_000_000,
        budget_period="monthly",
        rpm_limit=42,
        tpm_limit=123_000,
        max_parallel_requests=3,
        revision=1,
        updated_by=uuid4(),
        now=NOW,
    )


def _key(*, version: int = 1, retired: int | None = None) -> TenantModelGatewayKey:
    return TenantModelGatewayKey.restore(
        tenant_id=TENANT_ID,
        key_version=version,
        retired_key_version=retired,
        # observed 版本；对账前的入参用 retired（轮换时上一版本仍是网关上真实存在的那个）
        provisioned_key_version=retired,
        created_at=NOW,
        updated_at=NOW,
    )


def _digest(version: int) -> str:
    return tenant_gateway_key_digest(
        derive_tenant_gateway_key(
            secret=KEY_SECRET, tenant_id=TENANT_ID, key_version=version
        )
    )


def _admin() -> LiteLLMAdminClient:
    return LiteLLMAdminClient(
        base_url=SecretStr(os.environ["LITELLM_ADMIN_URL"]),
        master_key=SecretStr(os.environ["LITELLM_MASTER_KEY"]),
        timeout_seconds=30,
    )


async def _assert_inference_round_trip() -> None:
    """派生 Key 必须真的能完成一次推理——admin 侧有记录不等于凭据可用。"""
    import httpx

    provisioner = LiteLLMModelGatewayProvisioner(admin=_admin(), key_secret=KEY_SECRET)

    # 未对账的租户：其派生 Key 在网关侧根本不存在，必须被真实拒绝。
    unprovisioned = derive_tenant_gateway_key(
        secret=KEY_SECRET, tenant_id=uuid4(), key_version=1
    )
    async with httpx.AsyncClient(timeout=30) as client:
        rejected = await client.post(
            f"{os.environ['LITELLM_GATEWAY_URL']}/chat/completions",
            headers={"Authorization": f"Bearer {unprovisioned.get_secret_value()}"},
            json={"model": "general-purpose", "messages": [{"role": "user", "content": "ping"}]},
        )
    assert rejected.status_code in {401, 403}, (
        f"an unprovisioned tenant key must be rejected, got {rejected.status_code}"
    )
    print(f"unprovisioned tenant key correctly rejected: HTTP {rejected.status_code}")

    # 真实对账后，同一派生函数得到的 Key 必须能完成一次真实推理。
    await provisioner.apply_enabled(policy=_policy(), key=_key())
    credential = derive_tenant_gateway_key(
        secret=KEY_SECRET, tenant_id=TENANT_ID, key_version=1
    )
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{os.environ['LITELLM_GATEWAY_URL']}/chat/completions",
            headers={"Authorization": f"Bearer {credential.get_secret_value()}"},
            json={"model": "general-purpose", "messages": [{"role": "user", "content": "ping"}]},
        )
    assert response.status_code == 200, (
        f"provisioned tenant key failed inference: HTTP {response.status_code} {response.text}"
    )
    content = response.json()["choices"][0]["message"]["content"]
    assert content, "inference returned empty content"
    print(f"derived tenant key completed a real inference: HTTP 200 content={content!r}")

    await _admin().delete_key(TENANT_ID, _digest(1))
    print("real LiteLLM tenant key inference probe passed")


async def main() -> None:
    admin = _admin()
    provisioner = LiteLLMModelGatewayProvisioner(admin=admin, key_secret=KEY_SECRET)

    # 1. 首次对账：建租户聚合 + 签发可归因 Key
    await provisioner.apply_enabled(policy=_policy(), key=_key())
    record = await admin.get_key(_digest(1))
    assert record is not None, "real LiteLLM did not persist the tenant key"
    assert record.tenant_id == str(TENANT_ID), "key is not attributable to the tenant"
    assert record.blocked is False, "issued key must end unblocked"
    assert record.models == ("general-purpose",), f"unexpected models: {record.models}"
    print(f"issued attributable tenant key: alias={record.key_alias}")

    # 2. 幂等重放：同 revision 再对账不得报错、不得重复签发
    await provisioner.apply_enabled(policy=_policy(), key=_key())
    replayed = await admin.get_key(_digest(1))
    assert replayed == record, "replaying the same revision diverged"
    print("idempotent replay converged to the same key state")

    # 3. 轮换：新版本可用，旧版本在真实网关侧被删除
    await provisioner.apply_enabled(policy=_policy(), key=_key(version=2, retired=1))
    rotated = await admin.get_key(_digest(2))
    assert rotated is not None and rotated.blocked is False, "rotated key is unusable"
    assert await admin.get_key(_digest(1)) is None, "retired key still exists at the gateway"
    print("rotation issued v2 and retired v1 at the real gateway")

    # 4. 撤销：策略停用后 Key 在真实网关侧立即被阻断
    await provisioner.apply_disabled(policy=_policy(enabled=False), key=_key(version=2))
    revoked = await admin.get_key(_digest(2))
    assert revoked is not None and revoked.blocked is True, "revocation did not block the key"
    print("revocation blocked the tenant key at the real gateway")

    # 清理：本次探针创建的真实 Key 不留在网关里
    await admin.delete_key(TENANT_ID, _digest(2))
    assert await admin.get_key(_digest(2)) is None
    print("real LiteLLM tenant key reconcile probe passed")


if __name__ == "__main__":
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "reconcile"
        asyncio.run(_assert_inference_round_trip() if mode == "inference" else main())
    except AssertionError as error:
        print(f"tenant key probe failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
