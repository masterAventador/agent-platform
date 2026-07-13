from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.queue.redis_streams import (
    RunQueueDelivery,
    RunQueueMessage,
)
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.runtimes.base import RuntimeState
from agent_platform.workers.run_worker import RunWorker

BACKEND_ROOT = Path(__file__).parents[3]


class AllowAllRateLimiter:
    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        del scope, key


class OneDeliveryQueue:
    def __init__(self) -> None:
        self.delivery: RunQueueDelivery | None = None
        self.acknowledged: list[str] = []

    async def dequeue(self, *, consumer_name: str, block_ms: int) -> RunQueueDelivery | None:
        del consumer_name, block_ms
        delivery, self.delivery = self.delivery, None
        return delivery

    async def acknowledge(self, delivery_id: str) -> None:
        self.acknowledged.append(delivery_id)

    async def exhausted_delivery_attempts(self, delivery_id: str) -> int | None:
        del delivery_id
        return None


class WaitingAfterCancelRuntime:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.state = RuntimeState(
            run_id=run.id,
            status=RunStatus.WAITING_FOR_APPROVAL,
            data={},
        )
        self.events: list[PlatformEvent] = []

    async def cancel(self, run_id: UUID) -> None:
        assert run_id == self.state.run_id
        self.state = RuntimeState(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            data={},
        )
        self.events = [
            PlatformEvent.create(
                tenant_id=self.run.tenant_id,
                employee_id=self.run.employee_id,
                run_id=run_id,
                sequence=1,
                event_type=EventType.RUN_CANCELLED,
                payload={"status": "cancelled"},
            )
        ]

    async def get_state(self, run_id: UUID) -> RuntimeState:
        assert run_id == self.state.run_id
        return self.state

    def stream(self, run_id: UUID, *, after_sequence: int = 0) -> AsyncIterator[PlatformEvent]:
        del run_id, after_sequence

        async def iterate() -> AsyncIterator[PlatformEvent]:
            for event in self.events:
                yield event

        return iterate()


class RecordingPreparedRuntime:
    def __init__(self, runtime: WaitingAfterCancelRuntime) -> None:
        self.runtime = cast(Any, runtime)
        self.employee_definition: dict[str, object] = {}
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def detach(self) -> None:
        pass

    async def renew(self) -> None:
        pass


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 终态并发测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def postgres_runtime_client(
    migrated_postgres_url: str,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        settings=AppSettings(auth_cookie_secure=False),
        session_factory=session_factory,
        auth_rate_limiter=AllowAllRateLimiter(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_factory
    await engine.dispose()


async def _create_active_run(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    waiting_for_approval: bool,
) -> tuple[dict[str, str], Run, UUID]:
    credentials = {
        "email": f"terminal-race-{uuid4().hex}@example.com",
        "password": "correct horse battery staple",
    }
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    current_user = (await client.get("/api/v1/auth/me")).json()
    headers = {"X-Tenant-ID": current_user["workspaces"][0]["id"]}
    employee = (
        await client.post(
            "/api/v1/employees",
            headers=headers,
            json={
                "name": f"终态并发员工-{uuid4().hex[:8]}",
                "role_description": "验证终态并发串行化",
                "work_mode": "autonomous",
                "system_prompt": "按固定流程执行。",
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
    created = await client.post(
        f"/api/v1/employees/{employee['id']}/runs",
        headers=headers,
        json={"input": {}},
    )
    assert created.status_code == 201
    run_id = UUID(created.json()["id"])
    tenant_id = UUID(headers["X-Tenant-ID"])
    async with session_factory() as session:
        repository = SqlAlchemyRunRepository(session)
        run = await repository.get(tenant_id=tenant_id, run_id=run_id)
        assert run is not None
        active = run.transition_to(RunStatus.RUNNING)
        if waiting_for_approval:
            active = active.transition_to(RunStatus.WAITING_FOR_APPROVAL)
        await repository.update(active)
        command_id = (
            await session.execute(
                select(RunCommandRecord.id).where(RunCommandRecord.run_id == run_id)
            )
        ).scalar_one()
        await session.commit()
    return headers, active, command_id


async def _owned_worker(
    session_factory: async_sessionmaker[AsyncSession],
    run: Run,
) -> RunWorker:
    worker = RunWorker(
        session_factory=session_factory,
        queue=cast(Any, object()),
        runtime_resolver=cast(Any, object()),
        consumer_name="terminal-race-worker",
    )
    await worker._claim_ownership(run)
    return worker


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "reject"])
async def test_api_terminal_control_records_non_terminal_intent(
    postgres_runtime_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    action: str,
) -> None:
    client, session_factory = postgres_runtime_client
    headers, run, _ = await _create_active_run(
        client,
        session_factory,
        waiting_for_approval=action == "reject",
    )
    payload: dict[str, object] = {"action": action}
    if action == "reject":
        payload["approval_id"] = str(uuid4())
    response = await client.post(
        f"/api/v1/runs/{run.id}/control",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 202
    assert response.json()["status"] == run.status.value
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
    assert persisted is not None and persisted.status is run.status
    assert [event.type for event in events] == [EventType.RUN_PROGRESS]
    assert events[0].payload["action"] == f"{action}_requested"


@pytest.mark.asyncio
async def test_committed_cancel_intent_wins_over_late_worker_completion(
    postgres_runtime_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = postgres_runtime_client
    headers, run, command_id = await _create_active_run(
        client,
        session_factory,
        waiting_for_approval=False,
    )
    worker = await _owned_worker(session_factory, run)
    response = await client.post(
        f"/api/v1/runs/{run.id}/control",
        headers=headers,
        json={"action": "cancel"},
    )
    assert response.status_code == 202
    history = [
        PlatformEvent.create(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            run_id=run.id,
            sequence=1,
            event_type=EventType.RUN_COMPLETED,
            payload={"status": "completed"},
        )
    ]
    persisted_status = await worker._persist_runtime_result(
        run=run,
        message_command_id=command_id,
        state=RuntimeState(run_id=run.id, status=RunStatus.COMPLETED, data={}),
        history=history,
    )
    assert persisted_status is RunStatus.CANCELLED
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id,
            after_sequence=0,
        )
    assert persisted is not None and persisted.status is RunStatus.CANCELLED
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.type for event in events] == [
        EventType.RUN_PROGRESS,
        EventType.RUN_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_full_worker_path_acknowledges_cancel_before_releasing_runtime(
    postgres_runtime_client: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = postgres_runtime_client
    headers, run, _ = await _create_active_run(
        client,
        session_factory,
        waiting_for_approval=False,
    )
    queue = OneDeliveryQueue()
    worker = RunWorker(
        session_factory=session_factory,
        queue=cast(Any, queue),
        runtime_resolver=cast(Any, object()),
        consumer_name="terminal-cleanup-worker",
    )
    prepared = RecordingPreparedRuntime(WaitingAfterCancelRuntime(run))
    worker._prepared_runtimes[run.id] = cast(Any, prepared)
    worker._active_runs[run.id] = run
    await worker._claim_ownership(run)

    response = await client.post(
        f"/api/v1/runs/{run.id}/control",
        headers=headers,
        json={"action": "cancel"},
    )
    assert response.status_code == 202
    async with session_factory() as session:
        cancel_command_id = (
            await session.execute(
                select(RunCommandRecord.id)
                .where(
                    RunCommandRecord.run_id == run.id,
                    RunCommandRecord.action == "cancel",
                )
                .order_by(RunCommandRecord.created_at.desc())
            )
        ).scalar_one()
    queue.delivery = RunQueueDelivery(
        delivery_id="terminal-cleanup",
        message=RunQueueMessage(
            command_id=cancel_command_id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="cancel",
        ),
    )

    assert await worker.run_once(block_ms=1) is True

    assert queue.acknowledged == ["terminal-cleanup"]
    assert prepared.close_calls == 1
    assert run.id not in worker._prepared_runtimes
    async with session_factory() as session:
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
    assert ownership is not None and ownership.owner_id is None
