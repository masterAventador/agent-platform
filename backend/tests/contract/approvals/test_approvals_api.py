"""审批中心 API 契约测试（C13）。

覆盖：待办/历史列表与可见性、详情、批准/拒绝（含必填理由）、转交链与角色校验、
撤回、越权（member 403 / 跨租户 404）、过期 409、并发 CAS、决策幂等、
run control 入口与审批记录联动（不留旁路）。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.approvals import (
    SqlAlchemyApprovalRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
)
from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.tenants.memberships import TenantMembership, TenantRole


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
    transport = ASGITransport(app=app)
    yield app, session_factory, transport
    await engine.dispose()


async def _register(transport: ASGITransport, email: str) -> tuple[AsyncClient, dict[str, Any]]:
    client = AsyncClient(transport=transport, base_url="http://testserver")
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    return client, me.json()


async def _add_membership(
    session_factory: async_sessionmaker,
    *,
    tenant_id: UUID,
    user_id: UUID,
    role: TenantRole,
) -> None:
    membership = TenantMembership.create_owner(tenant_id=tenant_id, user_id=user_id)
    async with session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=membership.id,
                tenant_id=tenant_id,
                user_id=user_id,
                role=role.value,
                created_at=membership.created_at,
            )
        )
        await session.commit()


async def _seed_waiting_run(
    session_factory: async_sessionmaker,
    *,
    tenant_id: UUID,
    created_by: UUID,
) -> Run:
    run = Run.create(
        tenant_id=tenant_id,
        employee_id=uuid4(),
        employee_version=1,
        created_by=created_by,
        input_data={"task": "外部工具审批"},
    )
    run = run.transition_to(RunStatus.RUNNING).transition_to(RunStatus.WAITING_FOR_APPROVAL)
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await session.commit()
    return run


async def _seed_approval(
    session_factory: async_sessionmaker,
    *,
    run: Run,
    assignee_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    approval = Approval.create(
        tenant_id=run.tenant_id,
        source=ApprovalSource.TOOL_RISK,
        approval_type="tool.invocation",
        risk_level="external",
        requested_by=run.created_by,
        request_key=f"tool:{run.id}:{uuid4()}",
        context={"tool_name": "send_email", "arguments": {"to": "user@example.com"}},
        run_id=run.id,
        invocation_id=uuid4(),
        employee_id=run.employee_id,
        assignee_id=assignee_id,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    async with session_factory() as session:
        await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
        await session.commit()
    return approval


async def _workspace_context(
    transport: ASGITransport,
    email: str,
) -> tuple[AsyncClient, UUID, UUID]:
    client, me = await _register(transport, email)
    tenant_id = UUID(me["workspaces"][0]["id"])
    return client, UUID(me["id"]), tenant_id


@pytest.mark.asyncio
async def test_pending_list_detail_and_visibility(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner@example.com")
    stranger, _, _ = await _workspace_context(transport, "stranger@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    listing = await owner.get(
        "/api/v1/approvals",
        params={"view": "pending"},
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(approval.id)
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["context"]["tool_name"] == "send_email"
    assert body["items"][0]["run_id"] == str(run.id)

    detail = await owner.get(
        f"/api/v1/approvals/{approval.id}",
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert detail.status_code == 200
    assert detail.json()["invocation_id"] == str(approval.invocation_id)

    # 跨租户：列表看不到，详情/操作 404
    other_listing = await stranger.get("/api/v1/approvals", params={"view": "pending"})
    assert other_listing.status_code == 200
    assert other_listing.json()["total"] == 0
    assert (
        await stranger.get(f"/api/v1/approvals/{approval.id}")
    ).status_code == 404
    assert (
        await stranger.post(f"/api/v1/approvals/{approval.id}/approve", json={})
    ).status_code == 404
    await owner.aclose()
    await stranger.aclose()


@pytest.mark.asyncio
async def test_member_cannot_see_or_decide_unassigned_approval(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner2@example.com")
    member, member_id, _ = await _workspace_context(transport, "member2@example.com")
    await _add_membership(
        session_factory, tenant_id=tenant_id, user_id=member_id, role=TenantRole.MEMBER
    )
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id)}
    listing = await member.get("/api/v1/approvals", params={"view": "pending"}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0

    denied = await member.post(
        f"/api/v1/approvals/{approval.id}/approve", json={}, headers=headers
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "permission_denied"
    await owner.aclose()
    await member.aclose()


@pytest.mark.asyncio
async def test_approve_updates_record_and_enqueues_run_command(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner3@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id)}
    response = await owner.post(
        f"/api/v1/approvals/{approval.id}/approve",
        json={"reason": "允许执行"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == str(owner_id)
    assert body["reason"] == "允许执行"

    async with session_factory() as session:
        commands = list(
            (
                await session.execute(
                    select(RunCommandRecord).where(RunCommandRecord.run_id == run.id)
                )
            ).scalars()
        )
    assert [command.action for command in commands] == ["approve"]
    assert commands[0].payload["approval_id"] == str(approval.invocation_id)

    # 已决策后重复操作 → 409
    conflict = await owner.post(
        f"/api/v1/approvals/{approval.id}/approve", json={}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "approval_not_pending"
    await owner.aclose()


@pytest.mark.asyncio
async def test_reject_requires_reason(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner4@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id)}
    missing = await owner.post(
        f"/api/v1/approvals/{approval.id}/reject", json={}, headers=headers
    )
    assert missing.status_code == 422

    rejected = await owner.post(
        f"/api/v1/approvals/{approval.id}/reject",
        json={"reason": "外部发送不被允许"},
        headers=headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    await owner.aclose()


@pytest.mark.asyncio
async def test_decision_on_expired_approval_returns_409_and_marks_expired(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner5@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(
        session_factory,
        run=run,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    headers = {"X-Tenant-ID": str(tenant_id)}
    listing = await owner.get("/api/v1/approvals", params={"view": "pending"}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["status"] == "expired"

    response = await owner.post(
        f"/api/v1/approvals/{approval.id}/approve", json={}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approval_expired"

    async with session_factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=tenant_id, approval_id=approval.id
        )
    assert stored is not None and stored.status is ApprovalStatus.EXPIRED
    await owner.aclose()


@pytest.mark.asyncio
async def test_withdraw_only_requester_then_decision_conflicts(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner6@example.com")
    admin, admin_id, _ = await _workspace_context(transport, "admin6@example.com")
    await _add_membership(
        session_factory, tenant_id=tenant_id, user_id=admin_id, role=TenantRole.ADMIN
    )
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id)}
    denied = await admin.post(
        f"/api/v1/approvals/{approval.id}/withdraw", json={}, headers=headers
    )
    assert denied.status_code == 403

    withdrawn = await owner.post(
        f"/api/v1/approvals/{approval.id}/withdraw",
        json={"reason": "不需要了"},
        headers=headers,
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"

    conflict = await admin.post(
        f"/api/v1/approvals/{approval.id}/approve", json={}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "approval_not_pending"
    await owner.aclose()
    await admin.aclose()


@pytest.mark.asyncio
async def test_transfer_validates_role_and_builds_chain(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner7@example.com")
    admin, admin_id, _ = await _workspace_context(transport, "admin7@example.com")
    member, member_id, _ = await _workspace_context(transport, "member7@example.com")
    await _add_membership(
        session_factory, tenant_id=tenant_id, user_id=admin_id, role=TenantRole.ADMIN
    )
    await _add_membership(
        session_factory, tenant_id=tenant_id, user_id=member_id, role=TenantRole.MEMBER
    )
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id)}
    # 转交给 member（角色不满足）→ 422
    invalid = await owner.post(
        f"/api/v1/approvals/{approval.id}/transfer",
        json={"assignee_email": "member7@example.com"},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "assignee_not_eligible"

    # 转交给非本租户用户 → 422
    outsider = await owner.post(
        f"/api/v1/approvals/{approval.id}/transfer",
        json={"assignee_email": "nobody@example.com"},
        headers=headers,
    )
    assert outsider.status_code == 422

    transferred = await owner.post(
        f"/api/v1/approvals/{approval.id}/transfer",
        json={"assignee_email": "admin7@example.com", "reason": "请管理员处理"},
        headers=headers,
    )
    assert transferred.status_code == 200
    child = transferred.json()
    assert child["assignee_id"] == str(admin_id)
    assert child["transferred_from_id"] == str(approval.id)

    # 原记录进入 transferred 终态，历史可查
    history = await owner.get(
        "/api/v1/approvals", params={"view": "history"}, headers=headers
    )
    assert history.status_code == 200
    assert {item["id"] for item in history.json()["items"]} == {str(approval.id)}

    # 指定审批人后其他管理员（含 owner）不能决策
    denied = await owner.post(
        f"/api/v1/approvals/{child['id']}/approve", json={}, headers=headers
    )
    assert denied.status_code == 403

    decided = await admin.post(
        f"/api/v1/approvals/{child['id']}/approve", json={}, headers=headers
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    await owner.aclose()
    await admin.aclose()
    await member.aclose()


@pytest.mark.asyncio
async def test_repeated_decision_with_idempotency_key_returns_original(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner8@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id), "Idempotency-Key": str(uuid4())}
    first = await owner.post(
        f"/api/v1/approvals/{approval.id}/approve", json={}, headers=headers
    )
    replay = await owner.post(
        f"/api/v1/approvals/{approval.id}/approve", json={}, headers=headers
    )
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["revision"] == first.json()["revision"]

    async with session_factory() as session:
        commands = list(
            (
                await session.execute(
                    select(RunCommandRecord).where(RunCommandRecord.run_id == run.id)
                )
            ).scalars()
        )
    assert [command.action for command in commands] == ["approve"]
    await owner.aclose()


@pytest.mark.asyncio
async def test_run_control_approve_settles_approval_record_without_bypass(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner9@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(session_factory, run=run)

    headers = {"X-Tenant-ID": str(tenant_id)}
    response = await owner.post(
        f"/api/v1/runs/{run.id}/control",
        json={"action": "approve", "approval_id": str(approval.invocation_id)},
        headers=headers,
    )
    assert response.status_code == 202

    async with session_factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=tenant_id, approval_id=approval.id
        )
        commands = list(
            (
                await session.execute(
                    select(RunCommandRecord).where(RunCommandRecord.run_id == run.id)
                )
            ).scalars()
        )
    assert stored is not None
    assert stored.status is ApprovalStatus.APPROVED
    assert stored.decided_by == owner_id
    assert [command.action for command in commands] == ["approve"]

    # 记录已终态后，run control 再次批准同一 invocation → 409（无旁路）
    conflict = await owner.post(
        f"/api/v1/runs/{run.id}/control",
        json={"action": "approve", "approval_id": str(approval.invocation_id)},
        headers=headers,
    )
    assert conflict.status_code == 409
    await owner.aclose()


@pytest.mark.asyncio
async def test_run_control_approve_on_expired_approval_conflicts(api) -> None:
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner10@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    approval = await _seed_approval(
        session_factory,
        run=run,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    headers = {"X-Tenant-ID": str(tenant_id)}
    response = await owner.post(
        f"/api/v1/runs/{run.id}/control",
        json={"action": "approve", "approval_id": str(approval.invocation_id)},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approval_expired"
    await owner.aclose()


@pytest.mark.asyncio
async def test_run_control_approve_without_approval_record_is_fail_closed(api) -> None:
    """安全 fail-closed：run 已进 WAITING_FOR_APPROVAL 却查无审批记录时，

    run 控制入口必须拒绝（409），不得静默回退老通道直发 raw approve/reject 命令。
    这封堵「WAITING_FOR_APPROVAL + 无记录」旁路窗口（例如 APPROVAL_REQUIRED
    事件 approval_id 非法未能建记录）。
    """
    _, session_factory, transport = api
    owner, owner_id, tenant_id = await _workspace_context(transport, "owner11@example.com")
    run = await _seed_waiting_run(session_factory, tenant_id=tenant_id, created_by=owner_id)
    # 故意不创建对应审批记录，模拟旁路窗口
    orphan_invocation_id = uuid4()

    headers = {"X-Tenant-ID": str(tenant_id)}
    response = await owner.post(
        f"/api/v1/runs/{run.id}/control",
        json={"action": "approve", "approval_id": str(orphan_invocation_id)},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approval_record_missing"

    # 关键：不得下发任何 raw approve 命令（fail-open 会留下一条）
    async with session_factory() as session:
        commands = list(
            (
                await session.execute(
                    select(RunCommandRecord).where(RunCommandRecord.run_id == run.id)
                )
            ).scalars()
        )
    assert commands == []
    await owner.aclose()
