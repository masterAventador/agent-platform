"""长期记忆生命周期 API 契约测试。

覆盖失败矩阵：租户隔离、跨用户/跨员工命名空间越权、敏感信息脱敏与
受控拒绝、超大内容上限、过期读取时判定、禁用/启用、删除后不可召回。
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.memories import MemoryRecord
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def memory_api() -> AsyncIterator[
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


async def _join_as_member(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    async with session_factory() as session:
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _create_employee(client: AsyncClient, *, tenant_id: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "name": "记忆验收员工",
            "role_description": "验证长期记忆",
            "work_mode": "autonomous",
            "system_prompt": "执行任务。",
            "model": {"kind": "gateway_alias", "alias": "general-purpose"},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "capabilities": {
                "conversation": True,
                "scheduled_tasks": False,
                "file_upload": False,
                "memory": True,
            },
        },
    )
    assert response.status_code == 201
    employee = response.json()
    publish = await client.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers={"X-Tenant-ID": tenant_id},
    )
    assert publish.status_code == 200
    return employee


@pytest.mark.asyncio
async def test_memory_lifecycle_create_search_correct_disable_delete(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, _, owner, _ = memory_api
    current_user = await _register_and_login(owner, "memory-owner@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    created = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "user", "content": "用户偏好中文邮件签名"},
    )
    assert created.status_code == 201
    memory = created.json()
    assert memory["scope"] == "user"
    assert memory["scope_ref"] == current_user["id"]
    assert memory["source"] == "manual"
    assert memory["status"] == "active"

    tenant_created = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "tenant", "content": "企业统一使用北京时区"},
    )
    assert tenant_created.status_code == 201
    assert tenant_created.json()["scope_ref"] == tenant_id

    listed = await owner.get("/api/v1/memories", headers=headers)
    assert listed.status_code == 200
    assert {item["content"] for item in listed.json()} == {
        "用户偏好中文邮件签名",
        "企业统一使用北京时区",
    }

    keyword = await owner.get("/api/v1/memories", headers=headers, params={"q": "时区"})
    assert [item["content"] for item in keyword.json()] == ["企业统一使用北京时区"]

    scoped = await owner.get("/api/v1/memories", headers=headers, params={"scope": "user"})
    assert [item["content"] for item in scoped.json()] == ["用户偏好中文邮件签名"]

    corrected = await owner.patch(
        f"/api/v1/memories/{memory['id']}",
        headers=headers,
        json={"content": "用户偏好英文邮件签名"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["content"] == "用户偏好英文邮件签名"

    future_expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    retimed = await owner.patch(
        f"/api/v1/memories/{memory['id']}",
        headers=headers,
        json={"expires_at": future_expiry, "confidence": 0.5},
    )
    assert retimed.status_code == 200
    assert retimed.json()["confidence"] == 0.5
    assert retimed.json()["expires_at"] is not None
    assert retimed.json()["expired"] is False

    disabled = await owner.patch(
        f"/api/v1/memories/{memory['id']}",
        headers=headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    active_only = await owner.get(
        "/api/v1/memories", headers=headers, params={"active_only": "true"}
    )
    assert [item["content"] for item in active_only.json()] == ["企业统一使用北京时区"]

    enabled = await owner.patch(
        f"/api/v1/memories/{memory['id']}",
        headers=headers,
        json={"status": "active"},
    )
    assert enabled.json()["status"] == "active"

    deleted = await owner.delete(f"/api/v1/memories/{memory['id']}", headers=headers)
    assert deleted.status_code == 204
    assert (
        await owner.get(f"/api/v1/memories/{memory['id']}", headers=headers)
    ).status_code == 404
    remaining = await owner.get("/api/v1/memories", headers=headers)
    assert [item["content"] for item in remaining.json()] == ["企业统一使用北京时区"]


@pytest.mark.asyncio
async def test_expired_memory_is_excluded_from_active_view_at_read_time(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, session_factory, owner, _ = memory_api
    current_user = await _register_and_login(owner, "memory-expiry@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    created = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "scope": "user",
            "content": "临时上下文记忆",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    async with session_factory() as session:
        record = (
            await session.execute(
                select(MemoryRecord).where(MemoryRecord.id == UUID(memory_id))
            )
        ).scalar_one()
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    active_only = await owner.get(
        "/api/v1/memories", headers=headers, params={"active_only": "true"}
    )
    assert active_only.json() == []
    full_view = await owner.get("/api/v1/memories", headers=headers)
    assert [item["id"] for item in full_view.json()] == [memory_id]
    assert full_view.json()[0]["expired"] is True


@pytest.mark.asyncio
async def test_sensitive_content_is_redacted_or_rejected(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, _, owner, _ = memory_api
    current_user = await _register_and_login(owner, "memory-sensitive@example.com")
    tenant_id = current_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    redacted = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "user", "content": "运营手机号 13812345678，偏好上午联系"},
    )
    assert redacted.status_code == 201
    assert "13812345678" not in redacted.json()["content"]
    assert "偏好上午联系" in redacted.json()["content"]

    rejected = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "user", "content": "password=OnlySecretValue123"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "memory_content_rejected"


@pytest.mark.asyncio
async def test_oversized_content_is_rejected(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, _, owner, _ = memory_api
    current_user = await _register_and_login(owner, "memory-oversize@example.com")
    tenant_id = current_user["workspaces"][0]["id"]

    response = await owner.post(
        "/api/v1/memories",
        headers={"X-Tenant-ID": tenant_id},
        json={"scope": "user", "content": "x" * 4001},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_member_cannot_write_tenant_or_employee_scope_and_cannot_touch_others(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, session_factory, owner, member = memory_api
    owner_user = await _register_and_login(owner, "memory-rbac-owner@example.com")
    tenant_id = owner_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    member_user = await _register_and_login(member, "memory-rbac-member@example.com")
    await _join_as_member(session_factory, tenant_id=tenant_id, user_id=member_user["id"])
    employee = await _create_employee(owner, tenant_id=tenant_id)

    # 成员不能写企业/员工级命名空间
    for payload in (
        {"scope": "tenant", "content": "越权企业记忆"},
        {"scope": "employee", "scope_ref": employee["id"], "content": "越权员工记忆"},
    ):
        response = await member.post("/api/v1/memories", headers=headers, json=payload)
        assert response.status_code == 403

    # 成员不能以他人身份写 user 级记忆
    forged = await member.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "user", "scope_ref": owner_user["id"], "content": "冒名记忆"},
    )
    assert forged.status_code == 403

    # 管理角色可以写企业/员工级
    tenant_memory = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "tenant", "content": "企业级规范记忆"},
    )
    assert tenant_memory.status_code == 201
    employee_memory = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "employee", "scope_ref": employee["id"], "content": "员工级经验"},
    )
    assert employee_memory.status_code == 201

    # 不存在的员工引用被拒绝
    ghost_employee = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "employee", "scope_ref": str(uuid4()), "content": "幽灵员工"},
    )
    assert ghost_employee.status_code == 404

    owner_personal = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "user", "content": "Owner 的个人记忆"},
    )
    assert owner_personal.status_code == 201
    owner_memory_id = owner_personal.json()["id"]

    # 成员列表看不到他人的 user 级记忆，但能看到企业/员工级
    member_view = await member.get("/api/v1/memories", headers=headers)
    assert {item["content"] for item in member_view.json()} == {
        "企业级规范记忆",
        "员工级经验",
    }

    # 成员无法读取/纠正/删除他人的个人记忆（按资源不存在处理）
    assert (
        await member.get(f"/api/v1/memories/{owner_memory_id}", headers=headers)
    ).status_code == 404
    assert (
        await member.patch(
            f"/api/v1/memories/{owner_memory_id}",
            headers=headers,
            json={"content": "篡改"},
        )
    ).status_code == 404
    assert (
        await member.delete(f"/api/v1/memories/{owner_memory_id}", headers=headers)
    ).status_code == 404

    # 成员可见但无权治理企业级记忆
    tenant_memory_id = tenant_memory.json()["id"]
    assert (
        await member.patch(
            f"/api/v1/memories/{tenant_memory_id}",
            headers=headers,
            json={"status": "disabled"},
        )
    ).status_code == 403
    assert (
        await member.delete(f"/api/v1/memories/{tenant_memory_id}", headers=headers)
    ).status_code == 403

    # 成员可以管理自己的个人记忆；管理员可治理成员记忆（纠正/禁用）
    member_personal = await member.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "user", "content": "成员的个人记忆"},
    )
    assert member_personal.status_code == 201
    member_memory_id = member_personal.json()["id"]
    assert (
        await member.patch(
            f"/api/v1/memories/{member_memory_id}",
            headers=headers,
            json={"content": "成员纠正后的记忆"},
        )
    ).status_code == 200
    admin_governance = await owner.patch(
        f"/api/v1/memories/{member_memory_id}",
        headers=headers,
        json={"status": "disabled"},
    )
    assert admin_governance.status_code == 200


@pytest.mark.asyncio
async def test_memories_are_tenant_isolated(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, _, owner, member = memory_api
    tenant_a_user = await _register_and_login(owner, "memory-tenant-a@example.com")
    tenant_a = tenant_a_user["workspaces"][0]["id"]
    tenant_b_user = await _register_and_login(member, "memory-tenant-b@example.com")
    tenant_b = tenant_b_user["workspaces"][0]["id"]

    created = await owner.post(
        "/api/v1/memories",
        headers={"X-Tenant-ID": tenant_a},
        json={"scope": "tenant", "content": "租户 A 的机密偏好"},
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    other_view = await member.get("/api/v1/memories", headers={"X-Tenant-ID": tenant_b})
    assert other_view.json() == []
    assert (
        await member.get(f"/api/v1/memories/{memory_id}", headers={"X-Tenant-ID": tenant_b})
    ).status_code == 404
    assert (
        await member.patch(
            f"/api/v1/memories/{memory_id}",
            headers={"X-Tenant-ID": tenant_b},
            json={"content": "跨租户篡改"},
        )
    ).status_code == 404
    assert (
        await member.delete(
            f"/api/v1/memories/{memory_id}", headers={"X-Tenant-ID": tenant_b}
        )
    ).status_code == 404
    # 非成员也不能用租户 A 的 header 访问
    assert (
        await member.get("/api/v1/memories", headers={"X-Tenant-ID": tenant_a})
    ).status_code in {403, 404}


@pytest.mark.asyncio
async def test_conversation_scope_requires_owned_conversation(
    memory_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient, AsyncClient],
) -> None:
    _, session_factory, owner, member = memory_api
    owner_user = await _register_and_login(owner, "memory-conv-owner@example.com")
    tenant_id = owner_user["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}
    member_user = await _register_and_login(member, "memory-conv-member@example.com")
    await _join_as_member(session_factory, tenant_id=tenant_id, user_id=member_user["id"])
    employee = await _create_employee(owner, tenant_id=tenant_id)

    conversation = (
        await owner.post(
            "/api/v1/conversations",
            headers=headers,
            json={"employee_id": employee["id"], "title": "记忆会话"},
        )
    ).json()

    owned = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "scope": "conversation",
            "scope_ref": conversation["id"],
            "content": "会话内已确认预算 5 万",
        },
    )
    assert owned.status_code == 201

    foreign = await member.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "scope": "conversation",
            "scope_ref": conversation["id"],
            "content": "越权会话记忆",
        },
    )
    assert foreign.status_code == 404

    ghost = await owner.post(
        "/api/v1/memories",
        headers=headers,
        json={"scope": "conversation", "scope_ref": str(uuid4()), "content": "幽灵会话"},
    )
    assert ghost.status_code == 404
