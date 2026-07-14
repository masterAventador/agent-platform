from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def run_client() -> AsyncIterator[AsyncClient]:
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

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
    await engine.dispose()


@pytest_asyncio.fixture
async def multi_workspace_run_api() -> AsyncIterator[
    tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient]
]:
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
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as owner,
        AsyncClient(transport=transport, base_url="http://testserver") as second_owner,
    ):
        yield app, session_factory, owner, second_owner
    await engine.dispose()


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


async def _create_cancel_requested_run(client: AsyncClient, tenant_id: str) -> dict[str, Any]:
    headers = {"X-Tenant-ID": tenant_id}
    employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": "SSE 租户契约员工",
                "role_description": "验证多工作区流隔离",
                "work_mode": "autonomous",
                "system_prompt": "按输入执行任务。",
                "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "capabilities": {
                    "conversation": False,
                    "scheduled_tasks": False,
                    "file_upload": False,
                },
            },
        )
    ).json()
    assert (
        await client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
    ).status_code == 200
    response = await client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"task": "验证 SSE 租户"}},
    )
    assert response.status_code == 201
    run = response.json()
    assert (
        await client.post(
            f"/api/v1/runs/{run['id']}/control",
            headers=headers,
            json={"action": "cancel"},
        )
    ).status_code == 202
    return run


@pytest.mark.asyncio
async def test_create_and_read_queued_run_for_published_employee(run_client: AsyncClient) -> None:
    credentials = {
        "email": "run-owner@example.com",
        "password": "correct horse battery staple",
    }
    await run_client.post("/api/v1/auth/register", json=credentials)
    await run_client.post("/api/v1/auth/login", json=credentials)
    current_user = (await run_client.get("/api/v1/auth/me")).json()
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}
    employee_definition = {
        "name": "任务执行员工",
        "role_description": "用于任务契约验证",
        "work_mode": "autonomous",
        "system_prompt": "按输入执行任务。",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": False,
            "scheduled_tasks": False,
            "file_upload": False,
        },
    }
    employee = (
        await run_client.post("/api/v1/employees", headers=headers, json=employee_definition)
    ).json()
    await run_client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)

    create_response = await run_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"topic": "统一任务协议"}},
    )

    assert create_response.status_code == 201
    run = create_response.json()
    assert run["status"] == "queued"
    assert run["employee_version"] == 1
    assert run["thread_id"] == run["id"]
    assert run["input"] == {"topic": "统一任务协议"}

    get_response = await run_client.get(f"/api/v1/runs/{run['id']}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json() == run

    list_response = await run_client.get("/api/v1/runs", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json() == [run]

    events_response = await run_client.get(
        f"/api/v1/runs/{run['id']}/events?after_sequence=0",
        headers=headers,
    )
    assert events_response.status_code == 200
    assert events_response.json() == []

    cancel_response = await run_client.post(
        f"/api/v1/runs/{run['id']}/control",
        headers=headers,
        json={"action": "cancel"},
    )
    assert cancel_response.status_code == 202
    assert cancel_response.json()["status"] == "queued"

    events_response = await run_client.get(
        f"/api/v1/runs/{run['id']}/events?after_sequence=0",
        headers=headers,
    )
    assert events_response.status_code == 200
    assert [event["type"] for event in events_response.json()] == ["run.progress"]
    assert events_response.json()[0]["payload"]["action"] == "cancel_requested"


@pytest.mark.asyncio
async def test_member_run_access_is_owner_scoped_and_admin_can_manage_any_run(
    multi_workspace_run_api: tuple[
        FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient
    ],
) -> None:
    _, session_factory, owner_client, member_client = multi_workspace_run_api
    owner = await _register_and_login(owner_client, "run-rbac-owner@example.com")
    tenant_id = UUID(owner["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}
    employee = (
        await owner_client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": "Run RBAC 员工",
                "role_description": "验证任务所有权",
                "work_mode": "autonomous",
                "system_prompt": "只验证任务权限。",
                "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "capabilities": {
                    "conversation": True,
                    "scheduled_tasks": False,
                    "file_upload": False,
                },
            },
        )
    ).json()
    await owner_client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
    owner_run = (
        await owner_client.post(
            f"/api/v1/employees/{employee['id']}/runs",
            headers=headers,
            json={"input": {"owner": True}},
        )
    ).json()

    member = await _register_and_login(member_client, "run-rbac-member@example.com")
    async with session_factory() as session:
        membership = TenantMembershipRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=UUID(member["id"]),
            role="member",
            created_at=datetime.now(UTC),
        )
        session.add(membership)
        await session.commit()

    member_run_response = await member_client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"member": True}},
    )
    assert member_run_response.status_code == 201
    member_run = member_run_response.json()
    listed = await member_client.get("/api/v1/runs", headers=headers)
    assert [run["id"] for run in listed.json()] == [member_run["id"]]
    assert (
        await member_client.get(f"/api/v1/runs/{owner_run['id']}", headers=headers)
    ).status_code == 404
    assert (
        await member_client.get(
            f"/api/v1/runs/{owner_run['id']}/events",
            headers=headers,
        )
    ).status_code == 404
    assert (
        await member_client.get(
            f"/api/v1/runs/{owner_run['id']}/stream",
            headers=headers,
        )
    ).status_code == 404
    assert (
        await member_client.post(
            f"/api/v1/runs/{owner_run['id']}/control",
            headers=headers,
            json={"action": "cancel"},
        )
    ).status_code == 404

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        own_run = await runs.get(tenant_id=tenant_id, run_id=UUID(member_run["id"]))
        assert own_run is not None
        await runs.update(
            own_run.transition_to(RunStatus.RUNNING).transition_to(
                RunStatus.WAITING_FOR_APPROVAL
            )
        )
        await session.commit()
    member_approval = await member_client.post(
        f"/api/v1/runs/{member_run['id']}/control",
        headers=headers,
        json={"action": "approve", "approval_id": str(uuid4())},
    )
    assert member_approval.status_code == 403

    async with session_factory() as session:
        membership = (
            await session.execute(
                select(TenantMembershipRecord).where(
                    TenantMembershipRecord.tenant_id == tenant_id,
                    TenantMembershipRecord.user_id == UUID(member["id"]),
                )
            )
        ).scalar_one()
        membership.role = "admin"
        await session.commit()
    admin_list = await member_client.get("/api/v1/runs", headers=headers)
    assert {run["id"] for run in admin_list.json()} == {owner_run["id"], member_run["id"]}
    assert (
        await member_client.get(f"/api/v1/runs/{owner_run['id']}", headers=headers)
    ).status_code == 200
    assert (
        await member_client.post(
            f"/api/v1/runs/{member_run['id']}/control",
            headers=headers,
            json={"action": "approve", "approval_id": str(uuid4())},
        )
    ).status_code == 202


@pytest.mark.asyncio
async def test_member_cannot_probe_draft_employee_when_creating_run(
    multi_workspace_run_api: tuple[
        FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient
    ],
) -> None:
    _, session_factory, owner_client, member_client = multi_workspace_run_api
    owner = await _register_and_login(owner_client, "run-visibility-owner@example.com")
    tenant_id = UUID(owner["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}
    draft = (
        await owner_client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": "隐藏草稿",
                "role_description": "验证运行创建的资源防枚举",
                "work_mode": "autonomous",
                "system_prompt": "仅用于权限测试。",
                "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "capabilities": {
                    "conversation": True,
                    "scheduled_tasks": False,
                    "file_upload": False,
                },
            },
        )
    ).json()
    member = await _register_and_login(member_client, "run-visibility-member@example.com")
    async with session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=UUID(member["id"]),
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    hidden = await member_client.post(
        f"/api/v1/employees/{draft['id']}/runs",
        headers=headers,
        json={"input": {}},
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "resource_not_found"

    owner_draft = await owner_client.post(
        f"/api/v1/employees/{draft['id']}/runs",
        headers=headers,
        json={"input": {}},
    )
    assert owner_draft.status_code == 409
    assert owner_draft.json()["detail"]["code"] == "employee_not_published"


@pytest.mark.asyncio
async def test_stream_tenant_query_is_membership_scoped_and_unambiguous(
    multi_workspace_run_api: tuple[
        FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient
    ],
) -> None:
    _, session_factory, owner, second_owner = multi_workspace_run_api
    owner_user = await _register_and_login(owner, "stream-owner@example.com")
    second_user = await _register_and_login(second_owner, "stream-second@example.com")
    owner_tenant_id = owner_user["workspaces"][0]["id"]
    second_tenant_id = second_user["workspaces"][0]["id"]
    run = await _create_cancel_requested_run(owner, owner_tenant_id)

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        persisted_run = await runs.get(
            tenant_id=UUID(owner_tenant_id),
            run_id=UUID(run["id"]),
        )
        assert persisted_run is not None
        cancelled = persisted_run.transition_to(RunStatus.CANCELLED)
        await runs.update(cancelled)
        events = SqlAlchemyRunEventRepository(session)
        await events.append(
            PlatformEvent.create(
                tenant_id=cancelled.tenant_id,
                employee_id=cancelled.employee_id,
                run_id=cancelled.id,
                sequence=await events.next_sequence(run_id=cancelled.id),
                event_type=EventType.RUN_CANCELLED,
                payload={"status": "cancelled"},
            )
        )
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=UUID(second_tenant_id),
                user_id=UUID(owner_user["id"]),
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    cross_tenant = await owner.get(f"/api/v1/runs/{run['id']}/stream?tenant_id={second_tenant_id}")
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["detail"]["code"] == "resource_not_found"

    ambiguous = await owner.get(
        f"/api/v1/runs/{run['id']}/stream?tenant_id={second_tenant_id}",
        headers={"X-Tenant-ID": owner_tenant_id},
    )
    assert ambiguous.status_code == 400
    assert ambiguous.json()["detail"]["code"] == "tenant_context_conflict"

    consistent = await owner.get(
        f"/api/v1/runs/{run['id']}/stream?tenant_id={owner_tenant_id}",
        headers={"X-Tenant-ID": owner_tenant_id},
    )
    assert consistent.status_code == 200
    assert consistent.headers["content-type"].startswith("text/event-stream")
