from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def employee_clients() -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
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
        AsyncClient(transport=transport, base_url="http://testserver") as outsider,
    ):
        yield owner, outsider

    await engine.dispose()


async def register_and_login(client: AsyncClient, email: str) -> dict[str, object]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_create_update_publish_and_list_employee_versions(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    owner, _ = employee_clients
    current_user = await register_and_login(owner, "employee-owner@example.com")
    workspace = current_user["workspaces"][0]
    assert workspace["role"] == "owner"

    create_response = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": workspace["id"]},
        json={
            "name": "研究助理",
            "avatar_url": "https://assets.example.com/researcher.png",
            "role_description": "负责企业资料调研与报告整理",
            "visibility": "tenant",
            "work_mode": "autonomous",
            "system_prompt": "先核实信息来源，再形成结构化报告。",
            "model": {"provider": "openai", "name": "gpt-5"},
            "input_schema": {"type": "object", "required": ["topic"]},
            "output_schema": {"type": "object", "required": ["report"]},
            "capabilities": {
                "conversation": True,
                "scheduled_tasks": False,
                "file_upload": True,
            },
            "skill_ids": [],
            "tool_ids": [],
            "knowledge_base_ids": [],
            "approval_policy": {"high_risk_tools": "required"},
            "release_strategy": {"mode": "all"},
        },
    )

    assert create_response.status_code == 201
    employee = create_response.json()
    assert employee["name"] == "研究助理"
    assert employee["status"] == "draft"
    assert employee["published_version"] is None
    assert "runtime_type" not in employee
    assert employee["definition"]["avatar_url"].endswith("researcher.png")
    assert employee["definition"]["approval_policy"]["high_risk_tools"] == "required"

    update_response = await owner.put(
        f"/api/v1/employees/{employee['id']}",
        headers={"X-Tenant-ID": workspace["id"]},
        json={**create_response.json()["definition"], "name": "高级研究助理"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "高级研究助理"

    publish_response = await owner.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["status"] == "published"
    assert publish_response.json()["published_version"] == 1

    second_update = await owner.put(
        f"/api/v1/employees/{employee['id']}",
        headers={"X-Tenant-ID": workspace["id"]},
        json={**update_response.json()["definition"], "name": "首席研究助理"},
    )
    assert second_update.status_code == 200
    assert second_update.json()["status"] == "draft"
    assert second_update.json()["published_version"] == 1
    second_publish = await owner.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert second_publish.status_code == 200
    assert second_publish.json()["published_version"] == 2

    versions_response = await owner.get(
        f"/api/v1/employees/{employee['id']}/versions",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert versions_response.status_code == 200
    assert [version["version"] for version in versions_response.json()] == [2, 1]
    assert versions_response.json()[0]["definition"]["name"] == "首席研究助理"
    assert versions_response.json()[1]["definition"]["name"] == "高级研究助理"

    list_response = await owner.get(
        "/api/v1/employees",
        headers={"X-Tenant-ID": workspace["id"]},
    )
    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()] == ["首席研究助理"]


@pytest.mark.asyncio
async def test_employee_is_not_visible_across_tenants(
    employee_clients: tuple[AsyncClient, AsyncClient],
) -> None:
    owner, outsider = employee_clients
    owner_user = await register_and_login(owner, "tenant-one@example.com")
    outsider_user = await register_and_login(outsider, "tenant-two@example.com")
    owner_workspace = owner_user["workspaces"][0]
    outsider_workspace = outsider_user["workspaces"][0]

    created = await owner.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": owner_workspace["id"]},
        json={
            "name": "租户一员工",
            "role_description": "仅属于租户一",
            "work_mode": "workflow",
            "system_prompt": "按固定步骤执行。",
            "model": {"provider": "openai", "name": "gpt-5"},
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "capabilities": {
                "conversation": False,
                "scheduled_tasks": False,
                "file_upload": False,
            },
        },
    )
    assert created.status_code == 201

    hidden = await outsider.get(
        f"/api/v1/employees/{created.json()['id']}",
        headers={"X-Tenant-ID": outsider_workspace["id"]},
    )
    assert hidden.status_code == 404

    outsider_list = await outsider.get(
        "/api/v1/employees",
        headers={"X-Tenant-ID": outsider_workspace["id"]},
    )
    assert outsider_list.status_code == 200
    assert outsider_list.json() == []
