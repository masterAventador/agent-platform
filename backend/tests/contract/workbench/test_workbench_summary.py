from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.api.routes.workbench import WorkbenchSummaryResponse
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import SqlAlchemyRunRepository
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.platform.runs.entities import RunStatus


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def workbench_api() -> AsyncIterator[
    tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient]
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

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield app, session_factory, client

    await engine.dispose()


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


def _employee_definition(
    name: str,
    *,
    visibility: str = "tenant",
) -> dict[str, object]:
    return {
        "name": name,
        "role_description": "验证工作台真实统计",
        "visibility": visibility,
        "work_mode": "autonomous",
        "system_prompt": "按输入执行任务。",
        "model": {"kind": "gateway_alias", "alias": "general-purpose"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": True,
            "scheduled_tasks": False,
            "file_upload": False,
        },
    }


@pytest.mark.asyncio
async def test_owner_workbench_summary_aggregates_all_real_employee_and_run_statuses(
    workbench_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    _, session_factory, client = workbench_api
    current_user = await _register_and_login(client, "workbench-owner@example.com")
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}

    draft_employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json=_employee_definition("工作台草稿员工"),
        )
    ).json()
    published_employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json=_employee_definition("工作台已发布员工"),
        )
    ).json()
    assert (
        await client.post(
            f"/api/v1/employees/{published_employee['id']}/publish",
            headers=headers,
        )
    ).status_code == 200

    created_runs: list[dict[str, Any]] = []
    for index in range(7):
        response = await client.post(
            f"/api/v1/employees/{published_employee['id']}/runs",
            headers=headers,
            json={"input": {"index": index}},
        )
        assert response.status_code == 201
        created_runs.append(response.json())

    target_statuses = [
        RunStatus.RUNNING,
        RunStatus.WAITING_FOR_INPUT,
        RunStatus.WAITING_FOR_APPROVAL,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ]
    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        for run_data, target_status in zip(created_runs[1:], target_statuses, strict=True):
            run = await runs.get(
                tenant_id=UUID(draft_employee["tenant_id"]),
                run_id=UUID(run_data["id"]),
            )
            assert run is not None
            updated = run.transition_to(RunStatus.RUNNING)
            if target_status is not RunStatus.RUNNING:
                updated = updated.transition_to(
                    target_status,
                    error_code="controlled_failure" if target_status is RunStatus.FAILED else None,
                    error_message="受控失败" if target_status is RunStatus.FAILED else None,
                )
            await runs.update(updated)
        await session.commit()

    response = await client.get("/api/v1/workbench/summary", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "employees": {"total": 2, "draft": 1, "published": 1},
        "runs": {
            "total": 7,
            "queued": 1,
            "running": 1,
            "waiting_for_input": 1,
            "waiting_for_approval": 1,
            "completed": 1,
            "failed": 1,
            "cancelled": 1,
        },
    }


@pytest.mark.asyncio
async def test_member_workbench_summary_matches_existing_resource_visibility_rules(
    workbench_api: tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncClient],
) -> None:
    app, session_factory, owner_client = workbench_api
    owner = await _register_and_login(owner_client, "workbench-rbac-owner@example.com")
    tenant_id = owner["workspaces"][0]["id"]
    headers = {"X-Tenant-ID": tenant_id}

    tenant_employee = (
        await owner_client.post(
            "/api/v1/employees",
            headers=headers,
            json=_employee_definition("成员可见员工"),
        )
    ).json()
    private_employee = (
        await owner_client.post(
            "/api/v1/employees",
            headers=headers,
            json=_employee_definition("成员不可见私有员工", visibility="private"),
        )
    ).json()
    draft_employee = (
        await owner_client.post(
            "/api/v1/employees",
            headers=headers,
            json=_employee_definition("成员不可见草稿员工"),
        )
    ).json()
    assert draft_employee["status"] == "draft"
    for employee in (tenant_employee, private_employee):
        assert (
            await owner_client.post(
                f"/api/v1/employees/{employee['id']}/publish",
                headers=headers,
            )
        ).status_code == 200

    owner_run = await owner_client.post(
        f"/api/v1/employees/{tenant_employee['id']}/runs",
        headers=headers,
        json={"input": {"created_by": "owner"}},
    )
    assert owner_run.status_code == 201

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as member_client:
        member = await _register_and_login(
            member_client,
            "workbench-rbac-member@example.com",
        )
        async with session_factory() as session:
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(member["id"]),
                    role="member",
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()

        member_run = await member_client.post(
            f"/api/v1/employees/{tenant_employee['id']}/runs",
            headers=headers,
            json={"input": {"created_by": "member"}},
        )
        assert member_run.status_code == 201

        response = await member_client.get("/api/v1/workbench/summary", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "employees": {"total": 1, "draft": 0, "published": 1},
        "runs": {
            "total": 1,
            "queued": 1,
            "running": 0,
            "waiting_for_input": 0,
            "waiting_for_approval": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        },
    }


def _valid_summary_payload() -> dict[str, Any]:
    return {
        "employees": {"total": 1, "draft": 0, "published": 1},
        "runs": {
            "total": 0,
            "queued": 0,
            "running": 0,
            "waiting_for_input": 0,
            "waiting_for_approval": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        },
    }


def test_workbench_response_rejects_negative_counts() -> None:
    payload = _valid_summary_payload()
    payload["employees"] = {"total": -1, "draft": 0, "published": 0}

    with pytest.raises(ValidationError):
        WorkbenchSummaryResponse.model_validate(payload)


def test_workbench_response_rejects_string_counts_instead_of_coercing_them() -> None:
    payload = _valid_summary_payload()
    payload["employees"] = {"total": "1", "draft": 0, "published": 1}

    with pytest.raises(ValidationError):
        WorkbenchSummaryResponse.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            **_valid_summary_payload(),
            "future_section": {"count": 1},
        },
        {
            **_valid_summary_payload(),
            "runs": {
                **_valid_summary_payload()["runs"],
                "future_status": 1,
            },
        },
    ],
    ids=["top-level-extra", "nested-extra"],
)
def test_workbench_response_rejects_unknown_fields(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkbenchSummaryResponse.model_validate(payload)
