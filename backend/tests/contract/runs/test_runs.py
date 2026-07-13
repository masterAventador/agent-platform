from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord


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


async def _create_cancelled_run(client: AsyncClient, tenant_id: str) -> dict[str, Any]:
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
    ).status_code == 200
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
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    stream_response = await run_client.get(
        f"/api/v1/runs/{run['id']}/stream?tenant_id={headers['X-Tenant-ID']}",
    )
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.cancelled" in stream_response.text
    assert '"action": "cancel"' in stream_response.text


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
    run = await _create_cancelled_run(owner, owner_tenant_id)

    async with session_factory() as session:
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

    cross_tenant = await owner.get(
        f"/api/v1/runs/{run['id']}/stream?tenant_id={second_tenant_id}"
    )
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
