from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from capability_harness import CapabilityHarness
from sqlalchemy import update

from agent_platform.infrastructure.database.repositories.entitlements import (
    CapabilityEntitlementRecord,
)

_SOCIAL_PERMISSIONS = {"social.read", "social.manage", "social.execute"}


@pytest.mark.asyncio
async def test_registry_requires_authentication(
    capability_harness: CapabilityHarness,
) -> None:
    response = await capability_harness.client.get("/api/v1/capabilities/registry")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_registry_trims_unentitled_capability_declarations(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]

    response = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers={"X-Tenant-ID": tenant_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1.0"
    assert len(payload["capabilities"]) == 1
    entry = payload["capabilities"][0]
    assert entry == {
        "capability_id": "social-operations",
        "deployment_installed": True,
        "tenant_entitled": False,
    }


@pytest.mark.asyncio
async def test_owner_grant_produces_full_registry_entry_and_permissions(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={"source": "manual"},
    )
    assert grant.status_code == 200
    granted = grant.json()
    assert granted["capability_id"] == "social-operations"
    assert granted["status"] == "active"
    assert granted["expires_at"] is None

    registry = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers=headers,
    )
    entry = registry.json()["capabilities"][0]
    assert entry["deployment_installed"] is True
    assert entry["tenant_entitled"] is True
    assert set(entry["frontend_entries"]) == {"social.routes.v1"}
    assert set(entry["permissions"]) == _SOCIAL_PERMISSIONS

    me = await capability_harness.client.get("/api/v1/auth/me")
    permissions = set(me.json()["workspaces"][0]["permissions"])
    assert permissions >= _SOCIAL_PERMISSIONS


@pytest.mark.asyncio
async def test_member_sees_entitled_capability_without_declarations(
    capability_harness: CapabilityHarness,
) -> None:
    owner = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = owner["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={},
    )
    assert grant.status_code == 200
    await capability_harness.client.post("/api/v1/auth/logout")

    member = await capability_harness.register_and_login(f"member-{uuid4()}@example.com")
    await capability_harness.add_member(
        tenant_id=UUID(tenant_id),
        user_id=UUID(member["id"]),
        role="member",
    )

    registry = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers=headers,
    )
    assert registry.status_code == 200
    entries = {
        entry["capability_id"]: entry for entry in registry.json()["capabilities"]
    }
    entry = entries["social-operations"]
    assert entry["tenant_entitled"] is True
    assert "frontend_entries" not in entry
    assert "permissions" not in entry

    me = await capability_harness.client.get("/api/v1/auth/me")
    workspaces = {workspace["id"]: workspace for workspace in me.json()["workspaces"]}
    member_permissions = set(workspaces[tenant_id]["permissions"])
    assert not (_SOCIAL_PERMISSIONS & member_permissions)


@pytest.mark.asyncio
async def test_expired_entitlement_is_trimmed_from_registry(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={"expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    )
    assert grant.status_code == 200

    async with capability_harness.session_factory() as session:
        await session.execute(
            update(CapabilityEntitlementRecord)
            .where(CapabilityEntitlementRecord.tenant_id == UUID(tenant_id))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    registry = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers=headers,
    )
    entry = registry.json()["capabilities"][0]
    assert entry["tenant_entitled"] is False
    assert "frontend_entries" not in entry

    me = await capability_harness.client.get("/api/v1/auth/me")
    assert not (_SOCIAL_PERMISSIONS & set(me.json()["workspaces"][0]["permissions"]))


@pytest.mark.asyncio
async def test_core_only_profile_returns_empty_registry(
    core_only_harness: CapabilityHarness,
) -> None:
    current = await core_only_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]

    registry = await core_only_harness.client.get(
        "/api/v1/capabilities/registry",
        headers={"X-Tenant-ID": tenant_id},
    )

    assert registry.status_code == 200
    assert registry.json() == {"schema_version": "1.0", "capabilities": []}


@pytest.mark.asyncio
async def test_entitlements_are_tenant_isolated(
    capability_harness: CapabilityHarness,
) -> None:
    owner_a = await capability_harness.register_and_login(f"tenant-a-{uuid4()}@example.com")
    tenant_a = owner_a["workspaces"][0]["id"]
    grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers={"X-Tenant-ID": tenant_a},
        json={},
    )
    assert grant.status_code == 200
    await capability_harness.client.post("/api/v1/auth/logout")

    owner_b = await capability_harness.register_and_login(f"tenant-b-{uuid4()}@example.com")
    tenant_b = owner_b["workspaces"][0]["id"]
    registry = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers={"X-Tenant-ID": tenant_b},
    )
    entry = registry.json()["capabilities"][0]
    assert entry["tenant_entitled"] is False

    cross_tenant = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers={"X-Tenant-ID": tenant_a},
    )
    assert cross_tenant.status_code == 404


@pytest.mark.asyncio
async def test_grant_and_revoke_are_audited_and_idempotent(
    capability_harness: CapabilityHarness,
) -> None:
    current = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = current["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    first = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={},
    )
    second = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "active"

    listed = await capability_harness.client.get(
        "/api/v1/capabilities/entitlements",
        headers=headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    revoke = await capability_harness.client.delete(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"
    repeat_revoke = await capability_harness.client.delete(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
    )
    assert repeat_revoke.status_code == 200

    events = await capability_harness.client.get(
        "/api/v1/audit/events",
        headers=headers,
        params={"resource_type": "capability_entitlement"},
    )
    assert events.status_code == 200
    actions = [event["action"] for event in events.json()]
    assert "entitlement.granted" in actions
    assert "entitlement.revoked" in actions
    granted_event = next(
        event for event in events.json() if event["action"] == "entitlement.granted"
    )
    assert granted_event["metadata"]["capability_id"] == "social-operations"


@pytest.mark.asyncio
async def test_grant_validation_and_authorization_failures(
    capability_harness: CapabilityHarness,
) -> None:
    owner = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = owner["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    unknown = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/unknown-capability",
        headers=headers,
        json={},
    )
    assert unknown.status_code == 404

    past_expiry = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    )
    assert past_expiry.status_code == 400

    missing_revoke = await capability_harness.client.delete(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
    )
    assert missing_revoke.status_code == 404

    await capability_harness.client.post("/api/v1/auth/logout")
    member = await capability_harness.register_and_login(f"member-{uuid4()}@example.com")
    await capability_harness.add_member(
        tenant_id=UUID(tenant_id),
        user_id=UUID(member["id"]),
        role="member",
    )
    member_grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers=headers,
        json={},
    )
    assert member_grant.status_code == 403
    member_list = await capability_harness.client.get(
        "/api/v1/capabilities/entitlements",
        headers=headers,
    )
    assert member_list.status_code == 403


@pytest.mark.asyncio
async def test_grant_rejects_capability_not_installed_in_deployment(
    capability_harness: CapabilityHarness,
) -> None:
    """L3 硬化：Entitlement 只能授予当前部署已安装的能力，fail-closed。"""

    owner = await capability_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = owner["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    grant = await capability_harness.client.put(
        "/api/v1/capabilities/entitlements/video-studio",
        headers=headers,
        json={},
    )
    assert grant.status_code == 409
    assert grant.json()["detail"]["code"] == "capability_not_installed"

    listed = await capability_harness.client.get(
        "/api/v1/capabilities/entitlements",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json() == []

    registry = await capability_harness.client.get(
        "/api/v1/capabilities/registry",
        headers=headers,
    )
    capability_ids = {
        entry["capability_id"] for entry in registry.json()["capabilities"]
    }
    assert capability_ids == {"social-operations"}

    me = await capability_harness.client.get("/api/v1/auth/me")
    permissions = set(me.json()["workspaces"][0]["permissions"])
    assert not any(permission.startswith("video.") for permission in permissions)


@pytest.mark.asyncio
async def test_core_only_profile_rejects_all_entitlement_grants(
    core_only_harness: CapabilityHarness,
) -> None:
    owner = await core_only_harness.register_and_login(f"owner-{uuid4()}@example.com")
    tenant_id = owner["workspaces"][0]["id"]

    grant = await core_only_harness.client.put(
        "/api/v1/capabilities/entitlements/social-operations",
        headers={"X-Tenant-ID": tenant_id},
        json={},
    )
    assert grant.status_code == 409
    assert grant.json()["detail"]["code"] == "capability_not_installed"
