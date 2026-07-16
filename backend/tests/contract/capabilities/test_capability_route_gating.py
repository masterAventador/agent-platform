from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from capability_harness import CapabilityHarness

from agent_platform.infrastructure.database.repositories.entitlements import (
    SqlAlchemyCapabilityEntitlementRepository,
)


def _register_device_payload() -> dict[str, Any]:
    return {
        "device_id": str(uuid4()),
        "display_name": "契约测试设备",
        "platform": "macos",
        "app_version": "0.1.0",
        "executor_version": "1.0.0",
    }


async def _grant_social(harness: CapabilityHarness, headers: dict[str, str]) -> None:
    grant = await harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={},
    )
    assert grant.status_code == 200


@pytest.mark.asyncio
async def test_social_routes_require_authentication(
    capability_harness: CapabilityHarness,
) -> None:
    response = await capability_harness.client.post(
        "/api/v1/social-operations/devices/register",
        json=_register_device_payload(),
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unentitled_tenant_is_rejected_fail_closed(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    headers = {"X-Tenant-ID": current["workspaces"][0]["id"]}

    response = await capability_harness.client.post(
        "/api/v1/social-operations/devices/register",
        headers=headers,
        json=_register_device_payload(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "capability_not_entitled"


@pytest.mark.asyncio
async def test_entitled_owner_can_use_social_routes_with_audit_bridge(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_social(capability_harness, headers)

    response = await capability_harness.client.post(
        "/api/v1/social-operations/devices/register",
        headers=headers,
        json=_register_device_payload(),
    )
    assert response.status_code == 201
    assert response.json()["tenant_id"] == tenant_id

    events = await capability_harness.client.get(
        "/api/v1/audit/events",
        headers=headers,
        params={"action": "social.device.registered"},
    )
    assert events.status_code == 200
    matching = [
        event
        for event in events.json()
        if event["action"] == "social.device.registered"
    ]
    assert matching
    assert matching[0]["tenant_id"] == tenant_id
    assert matching[0]["resource_type"] == "social-operations"


@pytest.mark.asyncio
async def test_revocation_rejects_new_capability_calls(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_social(capability_harness, headers)

    first = await capability_harness.client.post(
        "/api/v1/social-operations/devices/register",
        headers=headers,
        json=_register_device_payload(),
    )
    assert first.status_code == 201

    revoke = await capability_harness.client.delete(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
    )
    assert revoke.status_code == 200

    second = await capability_harness.client.post(
        "/api/v1/social-operations/devices/register",
        headers=headers,
        json=_register_device_payload(),
    )
    assert second.status_code == 403
    assert second.json()["detail"]["code"] == "capability_not_entitled"

    listing = await capability_harness.client.get(
        "/api/v1/social-operations/devices",
        headers=headers,
    )
    assert listing.status_code == 403


@pytest.mark.asyncio
async def test_member_without_capability_permissions_is_rejected(
    capability_harness: CapabilityHarness,
) -> None:
    owner = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = owner["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_social(capability_harness, headers)
    await capability_harness.client.post("/api/v1/auth/logout")

    member = await capability_harness.register_and_login(f"member-{uuid4()}@example.com")
    await capability_harness.add_member(
        tenant_id=UUID(tenant_id),
        user_id=UUID(member["id"]),
        role="member",
    )

    response = await capability_harness.client.post(
        "/api/v1/social-operations/devices/register",
        headers=headers,
        json=_register_device_payload(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_core_only_profile_has_no_social_routes(
    core_only_harness: CapabilityHarness,
) -> None:
    current = await core_only_harness.register_and_login(f"owner-{uuid4()}@example.com")
    headers = {"X-Tenant-ID": current["workspaces"][0]["id"]}

    response = await core_only_harness.client.post(
        "/api/v1/social-operations/devices/register",
        headers=headers,
        json=_register_device_payload(),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_entitlement_lookup_failure_fails_closed(
    capability_harness: CapabilityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_social(capability_harness, headers)

    async def broken_get(self: object, **kwargs: object) -> None:
        raise RuntimeError("entitlement store unavailable")

    monkeypatch.setattr(SqlAlchemyCapabilityEntitlementRepository, "get", broken_get)

    from httpx import ASGITransport, AsyncClient

    cookies = capability_harness.client.cookies
    transport = ASGITransport(app=capability_harness.app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        cookies=cookies,
    ) as raw_client:
        response = await raw_client.post(
            "/api/v1/social-operations/devices/register",
            headers=headers,
            json=_register_device_payload(),
        )
        assert response.status_code == 500

        devices = await raw_client.get(
            "/api/v1/social-operations/devices",
            headers=headers,
        )
        assert devices.status_code == 500


@pytest.mark.asyncio
async def test_core_flows_remain_available_without_capability(
    core_only_harness: CapabilityHarness,
) -> None:
    """Core-only 交付 Profile：登录、员工、知识、Skill、工具入口保持可用。"""

    current = await core_only_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    employees = await core_only_harness.client.get("/api/v1/employees", headers=headers)
    assert employees.status_code == 200
    knowledge = await core_only_harness.client.get("/api/v1/knowledge-bases", headers=headers)
    assert knowledge.status_code == 200
    skills = await core_only_harness.client.get("/api/v1/skills", headers=headers)
    assert skills.status_code == 200
    tools = await core_only_harness.client.get("/api/v1/tools", headers=headers)
    assert tools.status_code == 200


@pytest.mark.asyncio
async def test_audit_flush_failure_after_business_success_returns_500(
    capability_harness: CapabilityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务成功但审计桥接失败时必须显式 500，禁止静默丢审计。

    已知不一致语义（记录于 roadmap C17）：能力服务的业务副作用（内存/SQLite 状态）
    已发生，客户端却收到 500；重试同一 device_id 会得到 409。设备注册幂等化是 follow-up。
    """

    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    await _grant_social(capability_harness, headers)

    import agent_platform.api.dependencies.capabilities as capability_dependencies

    async def broken_emit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(capability_dependencies, "emit_audit_event", broken_emit)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=capability_harness.app, raise_app_exceptions=False)
    payload = _register_device_payload()
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        cookies=capability_harness.client.cookies,
    ) as raw_client:
        response = await raw_client.post(
            "/api/v1/social-operations/devices/register",
            headers=headers,
            json=payload,
        )
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "capability_audit_flush_failed"

    # 审计事件必须没有落库（失败即失败，不允许半写）。
    events = await capability_harness.client.get(
        "/api/v1/audit/events",
        headers=headers,
        params={"action": "social.device.registered"},
    )
    assert events.status_code == 200
    assert [
        event for event in events.json() if event["action"] == "social.device.registered"
    ] == []
