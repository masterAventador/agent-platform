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
        "work_mode": "workflow",
        "system_prompt": "按输入执行任务。",
        "model": {"provider": "openai", "name": "gpt-5"},
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
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    stream_response = await run_client.get(
        f"/api/v1/runs/{run['id']}/stream",
        headers=headers,
    )
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.cancelled" in stream_response.text
    assert '"action": "cancel"' in stream_response.text
