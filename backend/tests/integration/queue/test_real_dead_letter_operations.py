import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunRecord,
    SqlAlchemyRunCommandRepository,
)
from agent_platform.infrastructure.queue.dead_letters import RunDeadLetterService
from agent_platform.infrastructure.queue.redis_streams import RunQueueDelivery, RunQueueMessage

BACKEND_ROOT = Path(__file__).parents[3]


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


@pytest.fixture(scope="module")
def postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实死信运维集成测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


async def _register_and_login(client: AsyncClient) -> dict[str, Any]:
    credentials = {
        "email": f"dead-letter-real-{uuid4()}@example.com",
        "password": "correct horse battery staple",
    }
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    return (await client.get("/api/v1/auth/me")).json()


@pytest.mark.asyncio
async def test_real_postgres_dead_letter_service_and_api(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(database_url=postgres_url, auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    service = RunDeadLetterService(session_factory=session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        current_user = await _register_and_login(client)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as original_creator_client:
            original_creator = await _register_and_login(original_creator_client)
        tenant_id = current_user["workspaces"][0]["id"]
        headers = {"X-Tenant-ID": tenant_id}
        employee = (
            await client.post(
                "/api/v1/employees",
                headers=headers,
                json={
                    "name": "真实死信恢复员工",
                    "role_description": "验证 PostgreSQL 死信恢复",
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
        assert (
            await client.post(
                f"/api/v1/employees/{employee['id']}/publish",
                headers=headers,
            )
        ).status_code == 200
        original = (
            await client.post(
                f"/api/v1/employees/{employee['id']}/runs",
                headers=headers,
                json={"input": {"task": "real postgres replay"}},
            )
        ).json()
        async with session_factory() as session:
            command_record = (
                await session.execute(
                    select(RunCommandRecord).where(
                        RunCommandRecord.run_id == UUID(original["id"])
                    )
                )
            ).scalar_one()
            original_record = await session.get(RunRecord, UUID(original["id"]))
            assert original_record is not None
            original_record.created_by = UUID(original_creator["id"])
            await session.commit()

        valid = await service.record_failure(
            RunQueueDelivery(
                delivery_id=f"real-{uuid4()}",
                message=RunQueueMessage(
                    command_id=command_record.id,
                    run_id=UUID(original["id"]),
                    tenant_id=UUID(tenant_id),
                    action="start",
                ),
            ),
            attempts=5,
            error_type="delivery_processing_failed",
        )
        verified_malformed = await service.record_malformed(
            delivery_id=f"real-verified-malformed-{uuid4()}",
            attempts=5,
            error_type="malformed_queue_message",
            raw_fields={
                "command_id": str(command_record.id),
                "run_id": original["id"],
                "tenant_id": tenant_id,
                "action": "start",
                "payload": "real-secret-that-must-not-appear",
            },
        )
        unverified_malformed = await service.record_malformed(
            delivery_id=f"real-malformed-{uuid4()}",
            attempts=5,
            error_type="malformed_queue_message",
            raw_fields={
                "tenant_id": tenant_id,
                "payload": "real-secret-that-must-not-appear",
                "unexpected-secret-key": "real-secret-value",
            },
        )
        assert unverified_malformed.tenant_id is None
        await service.record_malformed(
            delivery_id=f"real-platform-malformed-{uuid4()}",
            attempts=5,
            error_type="malformed_queue_message",
            raw_fields={"payload": "platform-secret"},
        )

        response = await client.get("/api/v1/run-dead-letters", headers=headers)
        assert response.status_code == 200
        items = response.json()
        assert [item["id"] for item in items[:2]] == [
            str(verified_malformed.id),
            str(valid.id),
        ]
        assert str(unverified_malformed.id) not in {item["id"] for item in items}
        assert "real-secret-that-must-not-appear" not in response.text
        assert "real-secret-value" not in response.text
        assert "platform-secret" not in response.text

        second_run = (
            await client.post(
                f"/api/v1/employees/{employee['id']}/runs",
                headers=headers,
                json={"input": {"task": "must remain queued"}},
            )
        ).json()
        invalid_delivery_id = f"real-cross-splice-{uuid4()}"
        with pytest.raises(LookupError):
            await service.record_failure(
                RunQueueDelivery(
                    delivery_id=invalid_delivery_id,
                    message=RunQueueMessage(
                        command_id=command_record.id,
                        run_id=UUID(second_run["id"]),
                        tenant_id=UUID(tenant_id),
                        action="start",
                    ),
                ),
                attempts=5,
                error_type="delivery_processing_failed",
            )
        async with session_factory() as session:
            untouched_run = await session.get(RunRecord, UUID(second_run["id"]))
            assert untouched_run is not None and untouched_run.status == "queued"
            invalid_dead_letter = (
                await session.execute(
                    select(RunDeadLetterRecord).where(
                        RunDeadLetterRecord.original_delivery_id == invalid_delivery_id
                    )
                )
            ).scalar_one_or_none()
            assert invalid_dead_letter is None

        async with session_factory() as session:
            run_count_before = (
                await session.execute(
                    select(func.count()).select_from(RunRecord).where(
                        RunRecord.tenant_id == UUID(tenant_id)
                    )
                )
            ).scalar_one()
            command_count_before = (
                await session.execute(
                    select(func.count()).select_from(RunCommandRecord).where(
                        RunCommandRecord.tenant_id == UUID(tenant_id)
                    )
                )
            ).scalar_one()

        first, second = await asyncio.gather(
            client.post(
                f"/api/v1/run-dead-letters/{valid.id}/replay",
                headers=headers,
            ),
            client.post(
                f"/api/v1/run-dead-letters/{valid.id}/replay",
                headers=headers,
            ),
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        replayed_run_id = UUID(first.json()["run_id"])
        replayed_command_id = UUID(first.json()["command_id"])
        assert replayed_run_id != UUID(original["id"])
        assert replayed_command_id != command_record.id
        async with session_factory() as session:
            replayed = await SqlAlchemyRunCommandRepository(session).get(replayed_command_id)
            assert replayed is not None
            assert replayed.run_id == replayed_run_id
            assert replayed.action.value == "start"
            assert replayed.dispatched_at is None
            replayed_run = await session.get(RunRecord, replayed_run_id)
            assert replayed_run is not None
            assert replayed_run.created_by == UUID(current_user["id"])
            run_count_after = (
                await session.execute(
                    select(func.count()).select_from(RunRecord).where(
                        RunRecord.tenant_id == UUID(tenant_id)
                    )
                )
            ).scalar_one()
            command_count_after = (
                await session.execute(
                    select(func.count()).select_from(RunCommandRecord).where(
                        RunCommandRecord.tenant_id == UUID(tenant_id)
                    )
                )
            ).scalar_one()
            assert run_count_after == run_count_before + 1
            assert command_count_after == command_count_before + 1

    await engine.dispose()
