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
    RunCommandRecord,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.platform.runs.entities import RunStatus


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def conversation_api() -> AsyncIterator[
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
        AsyncClient(transport=transport, base_url="http://testserver") as member,
    ):
        yield app, session_factory, owner, member
    await engine.dispose()


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


async def _published_conversation_employee(
    client: AsyncClient,
    *,
    tenant_id: str,
    name: str = "多轮会话员工",
) -> dict[str, Any]:
    headers = {"X-Tenant-ID": tenant_id}
    employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": name,
                "role_description": "验证多轮会话",
                "work_mode": "autonomous",
                "system_prompt": "按多轮消息执行任务。",
                "model": {"kind": "gateway_alias", "alias": "general-purpose"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "capabilities": {
                    "conversation": True,
                    "scheduled_tasks": False,
                    "file_upload": True,
                },
            },
        )
    ).json()
    assert (
        await client.post(f"/api/v1/employees/{employee['id']}/publish", headers=headers)
    ).status_code == 200
    return employee


@pytest.mark.asyncio
async def test_create_conversation_append_message_and_list_timeline_with_run_relationship(
    conversation_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, _, owner, _ = conversation_api
    current_user = await _register_and_login(owner, "conversation-owner@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    employee = await _published_conversation_employee(owner, tenant_id=tenant_id)

    created = await owner.post(
        "/api/v1/conversations",
        headers=headers,
        json={"employee_id": employee["id"], "title": "竞品调研"},
    )
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["tenant_id"] == tenant_id
    assert conversation["employee_id"] == employee["id"]
    assert conversation["created_by"] == current_user["id"]
    assert conversation["title"] == "竞品调研"
    assert conversation["thread_id"].startswith(f"conversation:{conversation['id']}")

    appended = await owner.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": "请整理这轮需求", "attachment_ids": [], "dispatch": True},
    )

    assert appended.status_code == 202
    body = appended.json()
    assert body["message"]["role"] == "user"
    assert body["message"]["content"] == "请整理这轮需求"
    assert body["message"]["run_id"] == body["run"]["id"]
    assert body["run"]["conversation_id"] == conversation["id"]
    assert body["run"]["thread_id"] == conversation["thread_id"]
    assert body["run"]["created_by"] == current_user["id"]
    assert body["run_action"] == "started"

    detail = await owner.get(f"/api/v1/conversations/{conversation['id']}", headers=headers)
    assert detail.status_code == 200
    timeline = detail.json()
    assert [message["content"] for message in timeline["messages"]] == ["请整理这轮需求"]
    assert [run["id"] for run in timeline["runs"]] == [body["run"]["id"]]

    listed = await owner.get("/api/v1/conversations", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [conversation["id"]]

    run = await owner.get(f"/api/v1/runs/{body['run']['id']}", headers=headers)
    assert run.status_code == 200
    assert run.json()["conversation_id"] == conversation["id"]


@pytest.mark.asyncio
async def test_waiting_for_input_append_submits_real_message_command(
    conversation_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, session_factory, owner, _ = conversation_api
    current_user = await _register_and_login(owner, "conversation-input@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    employee = await _published_conversation_employee(owner, tenant_id=tenant_id)
    conversation = (
        await owner.post(
            "/api/v1/conversations",
            headers=headers,
            json={"employee_id": employee["id"], "title": "人工输入"},
        )
    ).json()
    first_turn = (
        await owner.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            headers=headers,
            json={"content": "先启动", "dispatch": True},
        )
    ).json()
    run_id = UUID(first_turn["run"]["id"])

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        run = await runs.get(tenant_id=UUID(tenant_id), run_id=run_id)
        assert run is not None
        await runs.update(
            run.transition_to(RunStatus.RUNNING).transition_to(RunStatus.WAITING_FOR_INPUT)
        )
        await session.commit()

    appended = await owner.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": "这是用户补充的真实内容", "dispatch": True},
    )

    assert appended.status_code == 202
    body = appended.json()
    assert body["run"]["id"] == str(run_id)
    assert body["run_action"] == "message_submitted"
    assert body["message"]["run_id"] == str(run_id)
    async with session_factory() as session:
        command = (
            await session.execute(
                select(RunCommandRecord)
                .where(RunCommandRecord.run_id == run_id)
                .order_by(RunCommandRecord.created_at.desc())
            )
        ).scalars().first()
    assert command is not None
    assert command.action == "message"
    assert command.payload["message"] == "这是用户补充的真实内容"


@pytest.mark.asyncio
async def test_member_conversation_access_is_owner_scoped_and_admin_can_manage_any_conversation(
    conversation_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, session_factory, owner, member = conversation_api
    owner_user = await _register_and_login(owner, "conversation-rbac-owner@example.com")
    tenant_id = UUID(owner_user["workspaces"][0]["id"])
    headers = {"X-Tenant-ID": str(tenant_id)}
    employee = await _published_conversation_employee(owner, tenant_id=str(tenant_id))
    owner_conversation = (
        await owner.post(
            "/api/v1/conversations",
            headers=headers,
            json={"employee_id": employee["id"], "title": "Owner 会话"},
        )
    ).json()

    member_user = await _register_and_login(member, "conversation-rbac-member@example.com")
    async with session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=UUID(member_user["id"]),
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    member_conversation = (
        await member.post(
            "/api/v1/conversations",
            headers=headers,
            json={"employee_id": employee["id"], "title": "Member 会话"},
        )
    ).json()
    member_list = await member.get("/api/v1/conversations", headers=headers)
    assert [item["id"] for item in member_list.json()] == [member_conversation["id"]]
    assert (
        await member.get(f"/api/v1/conversations/{owner_conversation['id']}", headers=headers)
    ).status_code == 404
    assert (
        await member.post(
            f"/api/v1/conversations/{owner_conversation['id']}/messages",
            headers=headers,
            json={"content": "越权追加", "dispatch": False},
        )
    ).status_code == 404

    async with session_factory() as session:
        membership = (
            await session.execute(
                select(TenantMembershipRecord).where(
                    TenantMembershipRecord.tenant_id == tenant_id,
                    TenantMembershipRecord.user_id == UUID(member_user["id"]),
                )
            )
        ).scalar_one()
        membership.role = "admin"
        await session.commit()

    admin_list = await member.get("/api/v1/conversations", headers=headers)
    assert {item["id"] for item in admin_list.json()} == {
        owner_conversation["id"],
        member_conversation["id"],
    }
    assert (
        await member.get(f"/api/v1/conversations/{owner_conversation['id']}", headers=headers)
    ).status_code == 200


@pytest.mark.asyncio
async def test_failed_conversation_retry_uses_same_thread_and_bounded_context(
    conversation_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, session_factory, owner, _ = conversation_api
    current_user = await _register_and_login(owner, "conversation-retry@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    employee = await _published_conversation_employee(owner, tenant_id=tenant_id)
    conversation = (
        await owner.post(
            "/api/v1/conversations",
            headers=headers,
            json={"employee_id": employee["id"], "title": "错误重试"},
        )
    ).json()
    for index in range(12):
        response = await owner.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            headers=headers,
            json={"content": f"历史消息 {index}", "dispatch": index == 0},
        )
        assert response.status_code in {201, 202}
    detail = (
        await owner.get(f"/api/v1/conversations/{conversation['id']}", headers=headers)
    ).json()
    failed_run_id = UUID(detail["runs"][0]["id"])

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        run = await runs.get(tenant_id=UUID(tenant_id), run_id=failed_run_id)
        assert run is not None
        await runs.update(
            run.transition_to(RunStatus.RUNNING).transition_to(
                RunStatus.FAILED,
                error_code="model_timeout",
            )
        )
        await session.commit()

    retry = await owner.post(
        f"/api/v1/conversations/{conversation['id']}/retry",
        headers=headers,
        json={"run_id": str(failed_run_id)},
    )

    assert retry.status_code == 202
    retried = retry.json()["run"]
    assert retried["thread_id"] == conversation["thread_id"]
    assert retried["input"]["retry_of_run_id"] == str(failed_run_id)
    context_messages = retried["input"]["conversation_context"]["messages"]
    assert 1 <= len(context_messages) <= 8
    assert [message["content"] for message in context_messages][-1] == "历史消息 11"


@pytest.mark.asyncio
async def test_append_during_active_run_persists_followup_intent_command(
    conversation_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    """活跃任务期间追加消息必须留下可派生的 followup 意图，且不进入执行队列。"""
    _, session_factory, owner, _ = conversation_api
    current_user = await _register_and_login(owner, "conversation-followup@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    employee = await _published_conversation_employee(owner, tenant_id=tenant_id)
    conversation = (
        await owner.post(
            "/api/v1/conversations",
            headers=headers,
            json={"employee_id": employee["id"], "title": "自动续跑"},
        )
    ).json()
    first_turn = (
        await owner.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            headers=headers,
            json={"content": "第一轮输入", "dispatch": True},
        )
    ).json()
    assert first_turn["run_action"] == "started"
    active_run_id = UUID(first_turn["run"]["id"])

    queued = await owner.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": "第二轮排队输入", "dispatch": True},
    )
    stored = await owner.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": "只存储不派发", "dispatch": False},
    )

    assert queued.status_code == 202
    queued_body = queued.json()
    assert queued_body["run_action"] == "queued_after_current"
    assert queued_body["run"]["id"] == str(active_run_id)
    assert queued_body["message"]["run_id"] is None
    assert stored.status_code == 202
    assert stored.json()["run_action"] == "stored"

    async with session_factory() as session:
        followups = (
            (
                await session.execute(
                    select(RunCommandRecord).where(
                        RunCommandRecord.run_id == active_run_id,
                        RunCommandRecord.action == "followup",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(followups) == 1
    followup = followups[0]
    assert followup.payload["message_id"] == queued_body["message"]["id"]
    assert followup.payload["requested_by"] == current_user["id"]
    # followup 意图命令不进入执行队列：创建时即视为已分发、仅由 Worker 结算时消费
    assert followup.dispatched_at is not None
    assert followup.processed_at is None
