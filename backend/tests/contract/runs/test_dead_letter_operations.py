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
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.runs import RunCommandRecord, RunRecord
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest_asyncio.fixture
async def dead_letter_api() -> AsyncIterator[
    tuple[FastAPI, async_sessionmaker[AsyncSession], tuple[AsyncClient, ...]]
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
        AsyncClient(transport=transport, base_url="http://testserver") as admin,
        AsyncClient(transport=transport, base_url="http://testserver") as member,
        AsyncClient(transport=transport, base_url="http://testserver") as outsider,
    ):
        yield app, session_factory, (owner, admin, member, outsider)

    await engine.dispose()


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    credentials = {"email": email, "password": "correct horse battery staple"}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    return response.json()


async def _create_original_run(client: AsyncClient, tenant_id: str) -> dict[str, Any]:
    headers = {"X-Tenant-ID": tenant_id}
    employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": "死信恢复员工",
                "role_description": "验证死信重放契约",
                "work_mode": "autonomous",
                "system_prompt": "恢复任务。",
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
    publish = await client.post(
        f"/api/v1/employees/{employee['id']}/publish",
        headers=headers,
    )
    assert publish.status_code == 200
    response = await client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {"task": "replay me"}},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_dead_letter_operations_are_tenant_safe_privileged_and_idempotent(
    dead_letter_api: tuple[
        FastAPI, async_sessionmaker[AsyncSession], tuple[AsyncClient, ...]
    ],
) -> None:
    _, session_factory, clients = dead_letter_api
    owner, admin, member, outsider = clients
    owner_user = await _register_and_login(owner, "dead-letter-owner@example.com")
    admin_user = await _register_and_login(admin, "dead-letter-admin@example.com")
    member_user = await _register_and_login(member, "dead-letter-member@example.com")
    outsider_user = await _register_and_login(outsider, "dead-letter-outsider@example.com")
    tenant_id = owner_user["workspaces"][0]["id"]
    outsider_tenant_id = outsider_user["workspaces"][0]["id"]
    run = await _create_original_run(owner, tenant_id)
    now = datetime.now(UTC)
    legal_id = uuid4()
    malformed_id = uuid4()
    pending_id = uuid4()

    async with session_factory() as session:
        command_id = (
            await session.execute(
                select(RunCommandRecord.id).where(
                    RunCommandRecord.run_id == UUID(run["id"])
                )
            )
        ).scalar_one()
        for user, role in ((admin_user, "admin"), (member_user, "member")):
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=UUID(tenant_id),
                    user_id=UUID(user["id"]),
                    role=role,
                    created_at=now,
                )
            )
        session.add_all(
            [
                RunDeadLetterRecord(
                    id=legal_id,
                    source_stream="agent-platform:runs",
                    original_delivery_id="contract-legal",
                    original_command_id=command_id,
                    original_run_id=UUID(run["id"]),
                    tenant_id=UUID(tenant_id),
                    action="start",
                    attempts=5,
                    error_type="delivery_processing_failed",
                    is_malformed=False,
                    raw_fields_summary={},
                    failed_at=now - timedelta(minutes=2),
                    replayed_run_id=None,
                    replayed_command_id=None,
                    replayed_at=None,
                    settled_run_id=UUID(run["id"]),
                    mirrored_at=None,
                ),
                RunDeadLetterRecord(
                    id=malformed_id,
                    source_stream="agent-platform:runs",
                    original_delivery_id="contract-malformed",
                    original_command_id=None,
                    original_run_id=None,
                    tenant_id=UUID(tenant_id),
                    action=None,
                    attempts=6,
                    error_type="malformed_queue_message",
                    is_malformed=True,
                    raw_fields_summary={
                        "known_field_keys": ["payload", "tenant_id", "secret-value"],
                        "unknown_fields": [
                            {"length": 12, "sha256": "a" * 64},
                            {"length": 12, "sha256": "secret-value"},
                        ],
                        "field_count": 3,
                        "total_bytes": 128,
                        "sha256": "secret-value",
                        "raw_value": "secret-value",
                    },
                    failed_at=now,
                    replayed_run_id=None,
                    replayed_command_id=None,
                    replayed_at=None,
                    settled_run_id=None,
                    mirrored_at=now,
                ),
                RunDeadLetterRecord(
                    id=pending_id,
                    source_stream="agent-platform:runs",
                    original_delivery_id="contract-pending",
                    original_command_id=command_id,
                    original_run_id=UUID(run["id"]),
                    tenant_id=UUID(tenant_id),
                    action="start",
                    attempts=5,
                    error_type="delivery_processing_failed",
                    is_malformed=False,
                    raw_fields_summary={},
                    failed_at=now - timedelta(minutes=1),
                    replayed_run_id=None,
                    replayed_command_id=None,
                    replayed_at=None,
                    settled_run_id=None,
                    mirrored_at=None,
                ),
                RunDeadLetterRecord(
                    id=uuid4(),
                    source_stream="agent-platform:runs",
                    original_delivery_id="platform-malformed",
                    original_command_id=None,
                    original_run_id=None,
                    tenant_id=None,
                    action=None,
                    attempts=5,
                    error_type="malformed_queue_message",
                    is_malformed=True,
                    raw_fields_summary={"sha256": "c" * 64},
                    failed_at=now + timedelta(minutes=1),
                    replayed_run_id=None,
                    replayed_command_id=None,
                    replayed_at=None,
                    settled_run_id=None,
                    mirrored_at=None,
                ),
            ]
        )
        await session.commit()

    owner_headers = {"X-Tenant-ID": tenant_id}
    list_response = await owner.get(
        "/api/v1/run-dead-letters?limit=3",
        headers=owner_headers,
    )
    assert list_response.status_code == 200
    items = list_response.json()
    assert [item["id"] for item in items] == [str(malformed_id), str(pending_id), str(legal_id)]
    assert items[0]["is_malformed"] is True
    serialized = list_response.text
    assert "platform-malformed" not in serialized
    assert "secret-value" not in serialized
    assert "source_stream" not in serialized
    assert "original_delivery_id" not in serialized
    assert all("tenant_id" not in item for item in items)

    admin_headers = {"X-Tenant-ID": tenant_id}
    assert (
        await admin.get("/api/v1/run-dead-letters", headers=admin_headers)
    ).status_code == 200
    first_replay = await admin.post(
        f"/api/v1/run-dead-letters/{legal_id}/replay",
        headers=admin_headers,
    )
    assert first_replay.status_code == 200
    assert set(first_replay.json()) == {"run_id", "command_id"}
    async with session_factory() as session:
        replayed_run = await session.get(RunRecord, UUID(first_replay.json()["run_id"]))
        assert replayed_run is not None
        assert replayed_run.created_by == UUID(admin_user["id"])
    repeated_replay = await owner.post(
        f"/api/v1/run-dead-letters/{legal_id}/replay",
        headers=owner_headers,
    )
    assert repeated_replay.status_code == 200
    assert repeated_replay.json() == first_replay.json()

    member_headers = {"X-Tenant-ID": tenant_id}
    assert (
        await member.get("/api/v1/run-dead-letters", headers=member_headers)
    ).status_code == 403
    assert (
        await member.post(
            f"/api/v1/run-dead-letters/{legal_id}/replay",
            headers=member_headers,
        )
    ).status_code == 403
    assert (
        await outsider.post(
            f"/api/v1/run-dead-letters/{legal_id}/replay",
            headers={"X-Tenant-ID": outsider_tenant_id},
        )
    ).status_code == 404

    malformed_response = await owner.post(
        f"/api/v1/run-dead-letters/{malformed_id}/replay",
        headers=owner_headers,
    )
    assert malformed_response.status_code == 409
    assert malformed_response.json()["detail"]["code"] == "dead_letter_not_replayable"
    pending_response = await owner.post(
        f"/api/v1/run-dead-letters/{pending_id}/replay",
        headers=owner_headers,
    )
    assert pending_response.status_code == 409
    assert pending_response.json()["detail"]["code"] == "dead_letter_not_settled"

    for limit in (0, 101):
        response = await owner.get(
            f"/api/v1/run-dead-letters?limit={limit}",
            headers=owner_headers,
        )
        assert response.status_code == 422


def test_openapi_describes_dead_letter_operations(
    dead_letter_api: tuple[
        FastAPI, async_sessionmaker[AsyncSession], tuple[AsyncClient, ...]
    ],
) -> None:
    app, _, _ = dead_letter_api
    schema = app.openapi()
    list_operation = schema["paths"]["/api/v1/run-dead-letters"]["get"]
    replay_operation = schema["paths"][
        "/api/v1/run-dead-letters/{dead_letter_id}/replay"
    ]["post"]

    assert list_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "items": {"$ref": "#/components/schemas/RunDeadLetterResponse"},
        "type": "array",
        "title": "Response List Run Dead Letters Api V1 Run Dead Letters Get",
    }
    assert {"200", "403", "404", "409", "422"}.issubset(replay_operation["responses"])
    response_schema = schema["components"]["schemas"]["RunDeadLetterResponse"]
    assert set(response_schema["properties"]) == {
        "id",
        "original_command_id",
        "original_run_id",
        "action",
        "attempts",
        "error_type",
        "is_malformed",
        "raw_fields_summary",
        "failed_at",
        "settled_run_id",
        "replayed_run_id",
        "replayed_command_id",
        "replayed_at",
        "mirrored_at",
    }
