"""工作流注册中心 API 契约：注册、加版本、发布、回滚、列表与错误语义。"""

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
async def client() -> AsyncIterator[AsyncClient]:
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
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as value:
        yield value
    await engine.dispose()


async def _login(client: AsyncClient, email: str) -> str:
    creds = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=creds)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=creds)).status_code == 200
    me = (await client.get("/api/v1/auth/me")).json()
    return str(me["workspaces"][0]["id"])


def _graph() -> dict[str, object]:
    return {
        "entrypoint": "collect",
        "nodes": [
            {"name": "collect", "type": "agent", "config": {"prompt": "收集"}, "next": "review"},
            {
                "name": "review",
                "type": "human_approval",
                "config": {"title": "请审批"},
                "next": "finish",
            },
            {"name": "finish", "type": "agent", "config": {"prompt": "总结"}, "next": None},
        ],
    }


@pytest.mark.asyncio
async def test_register_list_version_publish_rollback(client: AsyncClient) -> None:
    tenant_id = await _login(client, "wf-owner@example.com")
    headers = {"X-Tenant-ID": tenant_id}

    created = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"name": "客服工作流", "description": "标准客服", "graph": _graph()},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    workflow_id = body["id"]
    assert body["latest_version"] == 1
    assert body["published_version"] is None
    assert body["status"] == "draft"

    listed = await client.get("/api/v1/workflows", headers=headers)
    assert listed.status_code == 200
    assert any(w["id"] == workflow_id for w in listed.json())

    v2 = await client.post(
        f"/api/v1/workflows/{workflow_id}/versions",
        headers=headers,
        json={"description": "v2", "graph": _graph()},
    )
    assert v2.status_code == 200
    assert v2.json()["latest_version"] == 2

    published = await client.post(
        f"/api/v1/workflows/{workflow_id}/publish",
        headers=headers,
        json={"version": 2},
    )
    assert published.status_code == 200
    assert published.json()["published_version"] == 2
    assert published.json()["status"] == "published"

    rolled = await client.post(
        f"/api/v1/workflows/{workflow_id}/rollback",
        headers=headers,
        json={"version": 1},
    )
    assert rolled.status_code == 200
    assert rolled.json()["published_version"] == 1

    versions = await client.get(f"/api/v1/workflows/{workflow_id}/versions", headers=headers)
    assert versions.status_code == 200
    assert [v["version"] for v in versions.json()] == [2, 1]


@pytest.mark.asyncio
async def test_register_rejects_invalid_graph(client: AsyncClient) -> None:
    tenant_id = await _login(client, "wf-invalid@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    response = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "name": "坏流程",
            "description": "",
            "graph": {
                "entrypoint": "a",
                "nodes": [
                    {"name": "a", "type": "agent", "config": {}, "next": "b"},
                    {"name": "b", "type": "agent", "config": {}, "next": "a"},
                ],
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_workflow_graph"


@pytest.mark.asyncio
async def test_register_rejects_subflow_human_approval_at_registration(client: AsyncClient) -> None:
    """子流程内含人工审批节点 → 注册期即受控 422（不留「可发布不可运行」员工）。"""

    tenant_id = await _login(client, "wf-subflow-approval@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    response = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "name": "非法子流程",
            "description": "",
            "graph": {
                "entrypoint": "wrap",
                "nodes": [
                    {
                        "name": "wrap",
                        "type": "subflow",
                        "config": {
                            "graph": {
                                "entrypoint": "approve",
                                "nodes": [
                                    {
                                        "name": "approve",
                                        "type": "human_approval",
                                        "config": {},
                                        "next": None,
                                    }
                                ],
                            }
                        },
                        "next": None,
                    }
                ],
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_workflow_graph"


@pytest.mark.asyncio
async def test_duplicate_name_conflict(client: AsyncClient) -> None:
    tenant_id = await _login(client, "wf-dup@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    payload = {"name": "唯一名", "description": "", "graph": _graph()}
    first = await client.post("/api/v1/workflows", headers=headers, json=payload)
    assert first.status_code == 201
    conflict = await client.post("/api/v1/workflows", headers=headers, json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "workflow_name_exists"


@pytest.mark.asyncio
async def test_publish_unknown_version_not_found(client: AsyncClient) -> None:
    tenant_id = await _login(client, "wf-badver@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    created = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"name": "流程", "description": "", "graph": _graph()},
    )
    workflow_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/workflows/{workflow_id}/publish",
        headers=headers,
        json={"version": 9},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workflow_version_not_found"


@pytest.mark.asyncio
async def test_workflow_employee_end_to_end_binding(client: AsyncClient) -> None:
    """流程员工必须引用已注册工作流；未发布不能发布员工；发布后固化版本。"""

    tenant_id = await _login(client, "wf-bind@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    created = await client.post(
        "/api/v1/workflows",
        headers=headers,
        json={"name": "绑定流程", "description": "", "graph": _graph()},
    )
    workflow_id = created.json()["id"]

    employee_payload = {
        "name": "流程数字员工",
        "role_description": "跑固定流程",
        "work_mode": "workflow",
        "system_prompt": "按流程执行",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {"conversation": True, "scheduled_tasks": False, "file_upload": False},
        "workflow_id": workflow_id,
    }
    employee = await client.post("/api/v1/employees", headers=headers, json=employee_payload)
    assert employee.status_code == 201, employee.text
    employee_id = employee.json()["id"]
    assert employee.json()["definition"]["workflow_id"] == workflow_id

    # 工作流尚未发布 → 员工发布应被拒绝。
    blocked = await client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "employee_configuration_unavailable"

    # 发布工作流 v1 后员工可发布，且版本定义固化 workflow_version=1。
    assert (
        await client.post(
            f"/api/v1/workflows/{workflow_id}/publish", headers=headers, json={"version": 1}
        )
    ).status_code == 200
    ok = await client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
    assert ok.status_code == 200, ok.text
    versions = await client.get(f"/api/v1/employees/{employee_id}/versions", headers=headers)
    definition = versions.json()[0]["definition"]
    assert definition["workflow_id"] == workflow_id
    assert definition["workflow_version"] == 1


@pytest.mark.asyncio
async def test_employee_rejects_unregistered_workflow(client: AsyncClient) -> None:
    tenant_id = await _login(client, "wf-unreg@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    from uuid import uuid4

    employee_payload = {
        "name": "非法流程员工",
        "role_description": "引用不存在流程",
        "work_mode": "hybrid",
        "system_prompt": "x",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {"conversation": True, "scheduled_tasks": False, "file_upload": False},
        "workflow_id": str(uuid4()),
    }
    response = await client.post("/api/v1/employees", headers=headers, json=employee_payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "employee_workflow_not_bindable"
