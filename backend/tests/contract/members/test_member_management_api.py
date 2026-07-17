"""C15 企业成员管理 API 契约测试。

覆盖失败矩阵：越权（非 Owner 改角色/移除/转 Owner）、跨租户、Owner 唯一性与
最后一个 Owner 保护、自我操作边界、邀请过期/重放/跨租户、审计留痕。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.invitations import (
    TenantInvitationRecord,
)

PASSWORD = "correct horse battery staple"


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def api() -> AsyncIterator[tuple[FastAPI, async_sessionmaker, ASGITransport]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield app, session_factory, ASGITransport(app=app)
    await engine.dispose()


async def _register(transport: ASGITransport, email: str) -> tuple[AsyncClient, dict[str, Any]]:
    client = AsyncClient(transport=transport, base_url="http://testserver")
    credentials = {"email": email, "password": PASSWORD}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    me = (await client.get("/api/v1/auth/me")).json()
    return client, me


def _tenant_of(me: dict[str, Any]) -> str:
    return me["workspaces"][0]["id"]


async def _invite_and_accept(
    *,
    owner: AsyncClient,
    owner_tenant: str,
    invitee_email: str,
    role: str,
    transport: ASGITransport,
) -> tuple[AsyncClient, dict[str, Any]]:
    invite = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": owner_tenant},
        json={"email": invitee_email, "role": role},
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["token"]

    invitee, invitee_me = await _register(transport, invitee_email)
    accept = await invitee.post("/api/v1/invitations/accept", json={"token": token})
    assert accept.status_code == 200, accept.text
    return invitee, invitee_me


@pytest.mark.asyncio
async def test_owner_invites_member_who_accepts_and_appears_in_member_list(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)

    members_before = await owner.get(
        "/api/v1/tenant/members", headers={"X-Tenant-ID": tenant}
    )
    assert members_before.status_code == 200
    assert len(members_before.json()) == 1

    invitee, invitee_me = await _invite_and_accept(
        owner=owner,
        owner_tenant=tenant,
        invitee_email="member@example.com",
        role="member",
        transport=transport,
    )

    members_after = (
        await owner.get("/api/v1/tenant/members", headers={"X-Tenant-ID": tenant})
    ).json()
    roles = {m["email"]: m["role"] for m in members_after}
    assert roles == {"owner@example.com": "owner", "member@example.com": "member"}

    # invitee sees the shared tenant in their workspaces
    invitee_now = (await invitee.get("/api/v1/auth/me")).json()
    assert tenant in {w["id"] for w in invitee_now["workspaces"]}


@pytest.mark.asyncio
async def test_owner_changes_member_role(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    _, member_me = await _invite_and_accept(
        owner=owner,
        owner_tenant=tenant,
        invitee_email="member@example.com",
        role="member",
        transport=transport,
    )
    member_id = member_me["id"]

    response = await owner.patch(
        f"/api/v1/tenant/members/{member_id}/role",
        headers={"X-Tenant-ID": tenant},
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_change_role_to_owner_is_rejected(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    _, member_me = await _invite_and_accept(
        owner=owner,
        owner_tenant=tenant,
        invitee_email="member@example.com",
        role="member",
        transport=transport,
    )

    # 直接把成员提为 Owner 必须被拒绝：Owner 只能经显式的所有权转移产生。
    response = await owner.patch(
        f"/api/v1/tenant/members/{member_me['id']}/role",
        headers={"X-Tenant-ID": tenant},
        json={"role": "owner"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "owner_role_requires_transfer"

    # 成员角色未被改动，仍为 member（唯一 Owner 不变）。
    members = (
        await owner.get("/api/v1/tenant/members", headers={"X-Tenant-ID": tenant})
    ).json()
    roles = {m["email"]: m["role"] for m in members}
    assert roles == {"owner@example.com": "owner", "member@example.com": "member"}


@pytest.mark.asyncio
async def test_accepting_a_second_invitation_when_already_member_is_controlled_409(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)

    invitee, _ = await _register(transport, "member@example.com")
    first = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "member@example.com", "role": "member"},
    )
    assert (
        await invitee.post("/api/v1/invitations/accept", json={"token": first.json()["token"]})
    ).status_code == 200

    # 已是成员后，再签发并接受第二封邀请：受控 409，且不出现半状态。
    second = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "member@example.com", "role": "admin"},
    )
    second_id = second.json()["id"]
    replay = await invitee.post(
        "/api/v1/invitations/accept", json={"token": second.json()["token"]}
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "already_member"

    # 成员未被重复添加（仍 2 名成员，角色未被第二封邀请的 admin 改动）。
    members = (
        await owner.get("/api/v1/tenant/members", headers={"X-Tenant-ID": tenant})
    ).json()
    roles = {m["email"]: m["role"] for m in members}
    assert roles == {"owner@example.com": "owner", "member@example.com": "member"}

    # 第二封邀请保持 pending（未被误置终态、未残留半提交状态）。
    pending = (
        await owner.get("/api/v1/tenant/invitations", headers={"X-Tenant-ID": tenant})
    ).json()
    assert any(inv["id"] == second_id and inv["status"] == "pending" for inv in pending)


@pytest.mark.asyncio
async def test_cannot_demote_last_owner(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)

    response = await owner.patch(
        f"/api/v1/tenant/members/{owner_me['id']}/role",
        headers={"X-Tenant-ID": tenant},
        json={"role": "admin"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "last_owner_protected"


@pytest.mark.asyncio
async def test_cannot_remove_self(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)

    response = await owner.request(
        "DELETE",
        f"/api/v1/tenant/members/{owner_me['id']}",
        headers={"X-Tenant-ID": tenant},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cannot_remove_self"


@pytest.mark.asyncio
async def test_owner_transfer_swaps_roles(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    new_owner_client, admin_me = await _invite_and_accept(
        owner=owner,
        owner_tenant=tenant,
        invitee_email="admin@example.com",
        role="admin",
        transport=transport,
    )

    response = await owner.post(
        "/api/v1/tenant/members/transfer-owner",
        headers={"X-Tenant-ID": tenant},
        json={"user_id": admin_me["id"]},
    )
    assert response.status_code == 200

    # 转移后原 Owner 降为 Admin，失去成员管理权；由新 Owner 查看成员列表。
    denied = await owner.get("/api/v1/tenant/members", headers={"X-Tenant-ID": tenant})
    assert denied.status_code == 403

    members = (
        await new_owner_client.get(
            "/api/v1/tenant/members", headers={"X-Tenant-ID": tenant}
        )
    ).json()
    roles = {m["email"]: m["role"] for m in members}
    assert roles == {"owner@example.com": "admin", "admin@example.com": "owner"}


@pytest.mark.asyncio
async def test_non_owner_cannot_manage_members(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    member, member_me = await _invite_and_accept(
        owner=owner,
        owner_tenant=tenant,
        invitee_email="member@example.com",
        role="member",
        transport=transport,
    )

    # member tries to change owner's role
    forbidden = await member.patch(
        f"/api/v1/tenant/members/{owner_me['id']}/role",
        headers={"X-Tenant-ID": tenant},
        json={"role": "member"},
    )
    assert forbidden.status_code == 403

    forbidden_remove = await member.request(
        "DELETE",
        f"/api/v1/tenant/members/{owner_me['id']}",
        headers={"X-Tenant-ID": tenant},
    )
    assert forbidden_remove.status_code == 403

    forbidden_transfer = await member.post(
        "/api/v1/tenant/members/transfer-owner",
        headers={"X-Tenant-ID": tenant},
        json={"user_id": member_me["id"]},
    )
    assert forbidden_transfer.status_code == 403

    forbidden_invite = await member.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "someone@example.com", "role": "member"},
    )
    assert forbidden_invite.status_code == 403


@pytest.mark.asyncio
async def test_outsider_cannot_access_tenant_members(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    outsider, _ = await _register(transport, "outsider@example.com")

    response = await outsider.get(
        "/api/v1/tenant/members", headers={"X-Tenant-ID": tenant}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invitation_cannot_grant_owner_role(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)

    response = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "x@example.com", "role": "owner"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invitation_role_not_allowed"


@pytest.mark.asyncio
async def test_invitation_token_cannot_be_replayed(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    invite = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "member@example.com", "role": "member"},
    )
    token = invite.json()["token"]
    invitee, _ = await _register(transport, "member@example.com")
    assert (
        await invitee.post("/api/v1/invitations/accept", json={"token": token})
    ).status_code == 200

    replay = await invitee.post("/api/v1/invitations/accept", json={"token": token})
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "invitation_not_pending"


@pytest.mark.asyncio
async def test_expired_invitation_is_rejected(api) -> None:
    app, session_factory, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    invite = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "member@example.com", "role": "member"},
    )
    token = invite.json()["token"]
    async with session_factory() as session:
        await session.execute(
            update(TenantInvitationRecord).values(
                expires_at=datetime.now(UTC) - timedelta(days=1)
            )
        )
        await session.commit()

    invitee, _ = await _register(transport, "member@example.com")
    response = await invitee.post("/api/v1/invitations/accept", json={"token": token})
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "invitation_expired"


@pytest.mark.asyncio
async def test_invitation_email_must_match_accepting_user(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    invite = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "intended@example.com", "role": "member"},
    )
    token = invite.json()["token"]
    wrong_user, _ = await _register(transport, "someone.else@example.com")
    response = await wrong_user.post("/api/v1/invitations/accept", json={"token": token})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "invitation_email_mismatch"


@pytest.mark.asyncio
async def test_owner_can_revoke_pending_invitation(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    invite = await owner.post(
        "/api/v1/tenant/invitations",
        headers={"X-Tenant-ID": tenant},
        json={"email": "member@example.com", "role": "member"},
    )
    invitation_id = invite.json()["id"]

    pending = (
        await owner.get("/api/v1/tenant/invitations", headers={"X-Tenant-ID": tenant})
    ).json()
    assert len(pending) == 1

    revoke = await owner.request(
        "DELETE",
        f"/api/v1/tenant/invitations/{invitation_id}",
        headers={"X-Tenant-ID": tenant},
    )
    assert revoke.status_code == 204

    pending_after = (
        await owner.get("/api/v1/tenant/invitations", headers={"X-Tenant-ID": tenant})
    ).json()
    assert pending_after == []


@pytest.mark.asyncio
async def test_tenant_settings_can_be_renamed(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)

    response = await owner.patch(
        "/api/v1/tenant/settings",
        headers={"X-Tenant-ID": tenant},
        json={"name": "Acme 企业"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Acme 企业"


@pytest.mark.asyncio
async def test_member_management_actions_are_audited(api) -> None:
    app, _, transport = api
    owner, owner_me = await _register(transport, "owner@example.com")
    tenant = _tenant_of(owner_me)
    _, member_me = await _invite_and_accept(
        owner=owner,
        owner_tenant=tenant,
        invitee_email="member@example.com",
        role="member",
        transport=transport,
    )
    await owner.patch(
        f"/api/v1/tenant/members/{member_me['id']}/role",
        headers={"X-Tenant-ID": tenant},
        json={"role": "admin"},
    )

    events = (
        await owner.get("/api/v1/audit/events", headers={"X-Tenant-ID": tenant})
    ).json()
    actions = {event["action"] for event in events}
    assert "tenant.invitation_created" in actions
    assert "tenant.invitation_accepted" in actions
    assert "tenant.member_added" in actions
    assert "tenant.role_assigned" in actions
