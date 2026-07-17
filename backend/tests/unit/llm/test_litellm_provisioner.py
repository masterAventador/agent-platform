"""LiteLLM Provisioner 适配层契约（C16 阶段一）。

只经 ``LiteLLMAdminClient`` 的公开 HTTP 管理路由对账，把上游错误映射为平台端口语义；
派生 Key 明文只出现在发往 LiteLLM 的请求体里，绝不进入平台错误、状态或返回值。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
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
from agent_platform.platform.model_gateway.errors import (
    ModelGatewayProvisioningOutcomeUnknown,
    ModelGatewayProvisioningPermanent,
    ModelGatewayProvisioningTransient,
)

TENANT_ID = UUID("2efea627-2d99-4b8e-a7de-252c742b245b")
TENANT_USER_ID = f"agent-platform:tenant:{TENANT_ID}"
KEY_SECRET = SecretStr("model-gateway-key-secret-for-provisioner-tests")
MASTER_KEY = "sk-master-must-never-leak"
NOW = datetime(2026, 7, 17, tzinfo=UTC)
ALLOWED_ROUTES = [
    "/chat/completions",
    "/models",
    "/v1/chat/completions",
    "/v1/models",
]


def _raw_key(version: int) -> str:
    return derive_tenant_gateway_key(
        secret=KEY_SECRET, tenant_id=TENANT_ID, key_version=version
    ).get_secret_value()


def _digest(version: int) -> str:
    return tenant_gateway_key_digest(
        derive_tenant_gateway_key(secret=KEY_SECRET, tenant_id=TENANT_ID, key_version=version)
    )


def _policy(*, enabled: bool = True) -> TenantModelGatewayPolicy:
    return TenantModelGatewayPolicy.create_desired(
        tenant_id=TENANT_ID,
        enabled=enabled,
        allowed_aliases={"general-purpose"},
        budget_microusd=12_345_678,
        budget_period="monthly",
        rpm_limit=7,
        tpm_limit=9_000,
        max_parallel_requests=3,
        revision=1,
        updated_by=UUID("00000000-0000-0000-0000-0000000000ff"),
        now=NOW,
    )


def _key(*, version: int = 1, retired_version: int | None = None) -> TenantModelGatewayKey:
    return TenantModelGatewayKey.restore(
        tenant_id=TENANT_ID,
        key_version=version,
        retired_key_version=retired_version,
        created_at=NOW,
        updated_at=NOW,
    )


def _provisioner(transport: httpx.AsyncBaseTransport) -> LiteLLMModelGatewayProvisioner:
    return LiteLLMModelGatewayProvisioner(
        admin=LiteLLMAdminClient(
            base_url=SecretStr("http://litellm:4000"),
            master_key=SecretStr(MASTER_KEY),
            timeout_seconds=5,
            transport=transport,
        ),
        key_secret=KEY_SECRET,
    )


class _FakeGateway:
    """按 LiteLLM v1.86.2 公开管理路由的响应形状回放的最小替身。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.user: dict[str, object] | None = None
        self.keys: dict[str, dict[str, object]] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/user/info":
            if self.user is None:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(
                200, json={"user_id": TENANT_USER_ID, "user_info": self.user}
            )
        if path in {"/user/new", "/user/update"}:
            self.user = {
                "max_budget": 12.345678,
                "budget_duration": "1mo",
                "rpm_limit": 7,
                "tpm_limit": 9_000,
                "max_parallel_requests": 3,
                "models": ["general-purpose"],
            }
            return httpx.Response(200, json={"user_id": TENANT_USER_ID})
        if path == "/key/list":
            key_hash = request.url.params["key_hash"]
            found = self.keys.get(key_hash)
            keys = [] if found is None else [found]
            return httpx.Response(200, json={"keys": keys, "total_count": len(keys)})
        if path == "/key/generate":
            import json as _json

            body = _json.loads(request.content)
            import hashlib

            key_hash = hashlib.sha256(body["key"].encode()).hexdigest()
            self.keys[key_hash] = {
                "token": key_hash,
                "user_id": body["user_id"],
                "key_alias": body["key_alias"],
                "models": body["models"],
                "allowed_routes": body["allowed_routes"],
                "blocked": body["blocked"],
            }
            return httpx.Response(200, json={"key": "redacted"})
        if path in {"/key/block", "/key/unblock"}:
            import json as _json

            body = _json.loads(request.content)
            self.keys[body["key"]]["blocked"] = path == "/key/block"
            return httpx.Response(200, json={})
        if path == "/key/delete":
            import json as _json

            body = _json.loads(request.content)
            for key_hash in body["keys"]:
                self.keys.pop(key_hash, None)
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected admin route: {path}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.mark.asyncio
async def test_enabled_policy_provisions_a_tenant_scoped_unblocked_key() -> None:
    gateway = _FakeGateway()

    await _provisioner(gateway.transport()).apply_enabled(policy=_policy(), key=_key())

    record = gateway.keys[_digest(1)]
    assert record["user_id"] == TENANT_USER_ID
    assert record["blocked"] is False
    assert record["models"] == ["general-purpose"]
    assert sorted(record["allowed_routes"]) == ALLOWED_ROUTES  # type: ignore[arg-type]
    assert gateway.user is not None


@pytest.mark.asyncio
async def test_monthly_budget_is_mapped_to_the_litellm_duration_contract() -> None:
    gateway = _FakeGateway()

    await _provisioner(gateway.transport()).apply_enabled(policy=_policy(), key=_key())

    import json as _json

    created = next(
        _json.loads(request.content)
        for request in gateway.requests
        if request.url.path == "/user/new"
    )
    assert created["budget_duration"] == "1mo"
    assert created["max_budget"] == 12.345678
    assert created["rpm_limit"] == 7
    assert created["tpm_limit"] == 9_000
    assert created["max_parallel_requests"] == 3


@pytest.mark.asyncio
async def test_reapplying_the_same_key_version_is_idempotent() -> None:
    gateway = _FakeGateway()
    provisioner = _provisioner(gateway.transport())

    await provisioner.apply_enabled(policy=_policy(), key=_key())
    generate_calls = sum(1 for r in gateway.requests if r.url.path == "/key/generate")
    await provisioner.apply_enabled(policy=_policy(), key=_key())

    assert generate_calls == 1
    # 已存在且已解除阻断的同版本 Key 不得被重新生成
    assert sum(1 for r in gateway.requests if r.url.path == "/key/generate") == 1
    assert len(gateway.keys) == 1
    assert gateway.keys[_digest(1)]["blocked"] is False


@pytest.mark.asyncio
async def test_reapplying_re_blocks_nothing_and_only_unblocks_once() -> None:
    gateway = _FakeGateway()
    provisioner = _provisioner(gateway.transport())

    await provisioner.apply_enabled(policy=_policy(), key=_key())
    await provisioner.apply_enabled(policy=_policy(), key=_key())

    assert sum(1 for r in gateway.requests if r.url.path == "/key/unblock") == 1


@pytest.mark.asyncio
async def test_an_existing_key_whose_gateway_scope_drifted_fails_closed() -> None:
    """网关侧被人为改动/篡改的同摘要 Key 绝不复用：宁可受控失败也不放宽授权范围。"""
    gateway = _FakeGateway()
    gateway.keys[_digest(1)] = {
        "token": _digest(1),
        "user_id": TENANT_USER_ID,
        "key_alias": "agent-platform-tenant-tampered-v1",
        "models": ["general-purpose"],
        "allowed_routes": ALLOWED_ROUTES,
        "blocked": False,
    }

    with pytest.raises(ModelGatewayProvisioningPermanent):
        await _provisioner(gateway.transport()).apply_enabled(policy=_policy(), key=_key())


@pytest.mark.asyncio
async def test_an_existing_key_owned_by_another_tenant_fails_closed() -> None:
    gateway = _FakeGateway()
    gateway.keys[_digest(1)] = {
        "token": _digest(1),
        "user_id": "agent-platform:tenant:4892992f-0058-4ec4-80ab-00024f582947",
        "key_alias": "agent-platform-tenant-4892992f-0058-4ec4-80ab-00024f582947-v1",
        "models": ["general-purpose"],
        "allowed_routes": ALLOWED_ROUTES,
        "blocked": False,
    }

    with pytest.raises(ModelGatewayProvisioningPermanent):
        await _provisioner(gateway.transport()).apply_enabled(policy=_policy(), key=_key())


@pytest.mark.asyncio
async def test_rotation_deletes_the_retired_key_version_at_the_gateway() -> None:
    gateway = _FakeGateway()
    provisioner = _provisioner(gateway.transport())
    await provisioner.apply_enabled(policy=_policy(), key=_key(version=1))

    await provisioner.apply_enabled(policy=_policy(), key=_key(version=2, retired_version=1))

    assert _digest(1) not in gateway.keys
    assert gateway.keys[_digest(2)]["blocked"] is False


@pytest.mark.asyncio
async def test_disabled_policy_blocks_the_active_key_immediately() -> None:
    gateway = _FakeGateway()
    provisioner = _provisioner(gateway.transport())
    await provisioner.apply_enabled(policy=_policy(), key=_key())

    await provisioner.apply_disabled(policy=_policy(enabled=False), key=_key())

    assert gateway.keys[_digest(1)]["blocked"] is True


@pytest.mark.asyncio
async def test_disabling_also_retires_a_pending_previous_key_version() -> None:
    gateway = _FakeGateway()
    provisioner = _provisioner(gateway.transport())
    await provisioner.apply_enabled(policy=_policy(), key=_key(version=1))
    await provisioner.apply_enabled(policy=_policy(), key=_key(version=2, retired_version=1))
    await provisioner.apply_enabled(policy=_policy(), key=_key(version=3, retired_version=2))

    await provisioner.apply_disabled(
        policy=_policy(enabled=False), key=_key(version=3, retired_version=2)
    )

    assert gateway.keys[_digest(3)]["blocked"] is True
    assert _digest(2) not in gateway.keys


@pytest.mark.parametrize("status_code", [500, 502, 503, 429, 408])
@pytest.mark.asyncio
async def test_retryable_gateway_statuses_map_to_transient(status_code: int) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "boom"})

    with pytest.raises(ModelGatewayProvisioningTransient):
        await _provisioner(httpx.MockTransport(respond)).apply_enabled(
            policy=_policy(), key=_key()
        )


@pytest.mark.asyncio
async def test_transport_failures_map_to_transient_on_read_paths() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gateway down")

    with pytest.raises(ModelGatewayProvisioningTransient):
        await _provisioner(httpx.MockTransport(respond)).apply_enabled(
            policy=_policy(), key=_key()
        )


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
@pytest.mark.asyncio
async def test_client_errors_map_to_permanent_and_stop_burning_retries(
    status_code: int,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/info":
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(status_code, json={"detail": "rejected"})

    with pytest.raises(ModelGatewayProvisioningPermanent):
        await _provisioner(httpx.MockTransport(respond)).apply_enabled(
            policy=_policy(), key=_key()
        )


@pytest.mark.asyncio
async def test_unknown_mutation_outcomes_are_surfaced_as_unknown_not_transient() -> None:
    """写请求传输中断：可能已生效，必须让上层停在不确定终态而不是自动重试。"""
    gateway = _FakeGateway()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ReadTimeout("write may have landed")
        return gateway.handle(request)

    with pytest.raises(ModelGatewayProvisioningOutcomeUnknown):
        await _provisioner(httpx.MockTransport(respond)).apply_enabled(
            policy=_policy(), key=_key()
        )


@pytest.mark.asyncio
async def test_provisioning_errors_never_carry_key_material_or_master_key() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "rejected"})

    with pytest.raises(ModelGatewayProvisioningPermanent) as captured:
        await _provisioner(httpx.MockTransport(respond)).apply_enabled(
            policy=_policy(), key=_key()
        )

    rendered = []
    error: BaseException | None = captured.value
    while error is not None:
        rendered.append(f"{error!r}\n{error}")
        error = error.__cause__ or error.__context__
    text = "\n".join(rendered)
    assert _raw_key(1) not in text
    assert MASTER_KEY not in text
    assert KEY_SECRET.get_secret_value() not in text
