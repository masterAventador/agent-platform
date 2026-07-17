"""C12 定时任务 API 契约：CRUD、暂停/恢复、执行历史、校验与租户/行级隔离。"""

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


async def login(client: AsyncClient, email: str) -> str:
    creds = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=creds)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=creds)).status_code == 200
    me = (await client.get("/api/v1/auth/me")).json()
    return str(me["workspaces"][0]["id"])


async def publish_employee(
    client: AsyncClient, tenant_id: str, *, scheduled_tasks: bool = True
) -> str:
    created = await client.post(
        "/api/v1/employees",
        headers={"X-Tenant-ID": tenant_id},
        json={
            "name": "巡检员",
            "role_description": "定时巡检",
            "work_mode": "autonomous",
            "system_prompt": "你是巡检员",
            "model": {"kind": "gateway_alias", "alias": "general-purpose"},
            "input_schema": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
                "additionalProperties": False,
            },
            "output_schema": {"type": "object"},
            "capabilities": {
                "conversation": True,
                "scheduled_tasks": scheduled_tasks,
                "file_upload": False,
            },
        },
    )
    assert created.status_code == 201, created.text
    employee_id = created.json()["id"]
    published = await client.post(
        f"/api/v1/employees/{employee_id}/publish",
        headers={"X-Tenant-ID": tenant_id},
        json={},
    )
    assert published.status_code in {200, 201}, published.text
    return str(employee_id)


def cron_payload(employee_id: str, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "employee_id": employee_id,
        "name": "每小时巡检",
        "schedule": {
            "kind": "cron",
            "cron_expression": "0 * * * *",
            "timezone": "Asia/Shanghai",
        },
        "input": {"topic": "巡检"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_read_update_and_delete_a_cron_task(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-owner@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)

    created = await client.post(
        "/api/v1/scheduled-tasks", headers=headers, json=cron_payload(employee_id)
    )
    assert created.status_code == 201, created.text
    body = created.json()
    task_id = body["id"]
    assert body["enabled"] is True
    assert body["schedule"]["timezone"] == "Asia/Shanghai"
    assert body["next_run_at"] is not None
    assert body["misfire_policy"] == "skip"
    assert body["concurrency_policy"] == "skip"

    listed = await client.get("/api/v1/scheduled-tasks", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [task_id]

    fetched = await client.get(f"/api/v1/scheduled-tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "每小时巡检"

    updated = await client.patch(
        f"/api/v1/scheduled-tasks/{task_id}",
        headers=headers,
        json={
            "name": "每天巡检",
            "schedule": {
                "kind": "cron",
                "cron_expression": "0 9 * * *",
                "timezone": "Asia/Shanghai",
            },
            "input": {"topic": "日常巡检"},
            "misfire_policy": "run_once",
            "concurrency_policy": "allow",
            "max_retries": 2,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "每天巡检"
    assert updated.json()["misfire_policy"] == "run_once"
    assert updated.json()["max_retries"] == 2

    deleted = await client.delete(f"/api/v1/scheduled-tasks/{task_id}", headers=headers)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/scheduled-tasks/{task_id}", headers=headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_create_a_one_shot_reservation(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-once@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)

    created = await client.post(
        "/api/v1/scheduled-tasks",
        headers=headers,
        json=cron_payload(
            employee_id,
            schedule={
                "kind": "once",
                "run_at": "2099-01-01T03:00:00Z",
                "timezone": "Asia/Shanghai",
            },
        ),
    )

    assert created.status_code == 201, created.text
    assert created.json()["schedule"]["kind"] == "once"
    assert created.json()["next_run_at"] == "2099-01-01T03:00:00Z"


@pytest.mark.asyncio
async def test_a_reservation_in_the_past_is_rejected(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-past@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)

    response = await client.post(
        "/api/v1/scheduled-tasks",
        headers=headers,
        json=cron_payload(
            employee_id,
            schedule={"kind": "once", "run_at": "2020-01-01T03:00:00Z", "timezone": "UTC"},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "schedule_has_no_future_occurrence"


@pytest.mark.asyncio
async def test_an_invalid_cron_expression_is_rejected(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-badcron@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)

    response = await client.post(
        "/api/v1/scheduled-tasks",
        headers=headers,
        json=cron_payload(
            employee_id,
            schedule={"kind": "cron", "cron_expression": "99 * * * *", "timezone": "UTC"},
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_cron_expression"


@pytest.mark.asyncio
async def test_an_unknown_timezone_is_rejected(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-badtz@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)

    response = await client.post(
        "/api/v1/scheduled-tasks",
        headers=headers,
        json=cron_payload(
            employee_id,
            schedule={
                "kind": "cron",
                "cron_expression": "0 * * * *",
                "timezone": "Mars/Olympus_Mons",
            },
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_schedule_timezone"


@pytest.mark.asyncio
async def test_input_is_validated_against_the_published_schema(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-badinput@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)

    response = await client.post(
        "/api/v1/scheduled-tasks",
        headers=headers,
        json=cron_payload(employee_id, input={"unexpected": "值"}),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "run_input_schema_validation_failed"


@pytest.mark.asyncio
async def test_an_employee_without_the_capability_cannot_be_scheduled(
    client: AsyncClient,
) -> None:
    tenant_id = await login(client, "sched-nocap@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id, scheduled_tasks=False)

    response = await client.post(
        "/api/v1/scheduled-tasks", headers=headers, json=cron_payload(employee_id)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "scheduled_tasks_disabled"


@pytest.mark.asyncio
async def test_pause_and_resume_toggle_the_next_run(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-pause@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)
    task_id = (
        await client.post(
            "/api/v1/scheduled-tasks", headers=headers, json=cron_payload(employee_id)
        )
    ).json()["id"]

    paused = await client.post(f"/api/v1/scheduled-tasks/{task_id}/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["enabled"] is False
    assert paused.json()["next_run_at"] is None

    # 重复暂停是非法状态转换。
    again = await client.post(f"/api/v1/scheduled-tasks/{task_id}/pause", headers=headers)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "invalid_scheduled_task_transition"

    resumed = await client.post(f"/api/v1/scheduled-tasks/{task_id}/resume", headers=headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["enabled"] is True
    assert resumed.json()["next_run_at"] is not None


@pytest.mark.asyncio
async def test_execution_history_is_exposed_for_the_task(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-history@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)
    task_id = (
        await client.post(
            "/api/v1/scheduled-tasks", headers=headers, json=cron_payload(employee_id)
        )
    ).json()["id"]

    history = await client.get(
        f"/api/v1/scheduled-tasks/{task_id}/executions", headers=headers
    )

    assert history.status_code == 200
    assert history.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.asyncio
async def test_another_tenant_cannot_see_or_touch_the_task(client: AsyncClient) -> None:
    owner_tenant = await login(client, "sched-tenant-a@example.com")
    employee_id = await publish_employee(client, owner_tenant)
    task_id = (
        await client.post(
            "/api/v1/scheduled-tasks",
            headers={"X-Tenant-ID": owner_tenant},
            json=cron_payload(employee_id),
        )
    ).json()["id"]

    outsider_tenant = await login(client, "sched-tenant-b@example.com")
    headers = {"X-Tenant-ID": outsider_tenant}

    assert (
        await client.get(f"/api/v1/scheduled-tasks/{task_id}", headers=headers)
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/scheduled-tasks/{task_id}/pause", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/scheduled-tasks", headers=headers)).json()["items"] == []


@pytest.mark.asyncio
async def test_scheduling_an_unpublished_employee_is_rejected(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-draft@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    created = await client.post(
        "/api/v1/employees",
        headers=headers,
        json={
            "name": "草稿员工",
            "role_description": "草稿",
            "work_mode": "autonomous",
            "system_prompt": "草稿",
            "model": {"kind": "gateway_alias", "alias": "general-purpose"},
            "input_schema": {"type": "object", "additionalProperties": False},
            "output_schema": {"type": "object"},
            "capabilities": {
                "conversation": True,
                "scheduled_tasks": True,
                "file_upload": False,
            },
        },
    )
    employee_id = created.json()["id"]

    response = await client.post(
        "/api/v1/scheduled-tasks",
        headers=headers,
        json=cron_payload(employee_id, input={}),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "employee_not_published"


@pytest.mark.asyncio
async def test_creating_a_task_is_audited(client: AsyncClient) -> None:
    tenant_id = await login(client, "sched-audit@example.com")
    headers = {"X-Tenant-ID": tenant_id}
    employee_id = await publish_employee(client, tenant_id)
    await client.post("/api/v1/scheduled-tasks", headers=headers, json=cron_payload(employee_id))

    events = await client.get("/api/v1/audit/events", headers=headers)

    assert events.status_code == 200
    actions = [item["action"] for item in events.json()]
    assert "scheduled_task.created" in actions
