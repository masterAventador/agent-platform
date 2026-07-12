from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.auth import (
    SqlAlchemyUserRepository,
)
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnership,
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
)
from agent_platform.infrastructure.queue.dead_letters import (
    DeadLetterSettlementPending,
    RunDeadLetterService,
)
from agent_platform.infrastructure.queue.redis_streams import RunQueueDelivery, RunQueueMessage
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.users.entities import User

BACKEND_ROOT = Path(__file__).parents[3]


def _run_fixture() -> tuple[Tenant, User, Employee, Run, RunCommand]:
    tenant = Tenant.create(name="DLQ concurrency", slug=f"dlq-race-{uuid4().hex}")
    user = User.create(email=f"{uuid4().hex}@example.com", password_hash="hash")
    employee = Employee.create(
        tenant_id=tenant.id,
        created_by=user.id,
        draft=EmployeeDraft(
            name="DLQ employee",
            avatar_url=None,
            role_description="test",
            visibility=EmployeeVisibility.PRIVATE,
            runtime_type=RuntimeType.AUTONOMOUS,
            system_prompt="test",
            model_settings={},
            input_schema={},
            output_schema={},
            capabilities={},
            skill_ids=[],
            tool_ids=[],
            knowledge_base_ids=[],
            approval_policy={},
            release_strategy={},
        ),
    )
    run = Run.create(
        tenant_id=tenant.id,
        employee_id=employee.id,
        employee_version=1,
        created_by=user.id,
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command_record = RunCommand.create(
        run_id=run.id,
        tenant_id=tenant.id,
        action=RunCommandAction.MESSAGE,
    )
    return tenant, user, employee, run, command_record


async def _persist_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    fixture: tuple[Tenant, User, Employee, Run, RunCommand],
) -> None:
    tenant, user, employee, run, command_record = fixture
    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        await SqlAlchemyUserRepository(session).add(user)
        await SqlAlchemyEmployeeRepository(session).add(employee)
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command_record)
        await session.commit()


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 死信并发测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_postgres_malformed_settlement_serializes_with_runtime_takeover(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _run_fixture()
    tenant, _, _, run, malformed_command = fixture
    now = datetime.now(UTC)
    try:
        await _persist_fixture(session_factory, fixture)
        async with session_factory() as session:
            ownership = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
                run_id=run.id,
                tenant_id=tenant.id,
                owner_id="settling-worker",
                now=now,
                lease_duration=timedelta(seconds=30),
            )
            await session.commit()

        ownership_locked = asyncio.Event()
        allow_settlement = asyncio.Event()
        original_assert_owned = SqlAlchemyRuntimeOwnershipRepository.assert_owned

        async def assert_owned_then_pause(
            repository: SqlAlchemyRuntimeOwnershipRepository,
            *,
            run_id: UUID,
            owner_id: str,
            epoch: int,
            now: datetime,
        ) -> None:
            await original_assert_owned(
                repository,
                run_id=run_id,
                owner_id=owner_id,
                epoch=epoch,
                now=now,
            )
            ownership_locked.set()
            await allow_settlement.wait()

        monkeypatch.setattr(
            SqlAlchemyRuntimeOwnershipRepository,
            "assert_owned",
            assert_owned_then_pause,
        )
        service = RunDeadLetterService(session_factory=session_factory)
        settlement_task = asyncio.create_task(
            service.record_malformed(
                delivery_id=f"postgres-malformed-{uuid4()}",
                attempts=5,
                error_type="malformed_queue_message",
                raw_fields={
                    "command_id": str(malformed_command.id),
                    "run_id": str(run.id),
                    "tenant_id": str(tenant.id),
                    "action": "message",
                    "payload": "not-json",
                },
                ownerships=(ownership,),
            )
        )
        await ownership_locked.wait()

        async def take_over() -> RuntimeOwnership:
            async with session_factory() as session:
                claimed = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
                    run_id=run.id,
                    tenant_id=tenant.id,
                    owner_id="replacement-worker",
                    now=now + timedelta(seconds=31),
                    lease_duration=timedelta(seconds=30),
                )
                await session.commit()
                return claimed

        takeover_task = asyncio.create_task(take_over())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(takeover_task), timeout=0.05)
        allow_settlement.set()
        dead_letter = await settlement_task
        replacement = await takeover_task

        assert dead_letter.settled_run_id == run.id
        assert replacement.owner_id == "replacement-worker"
        async with session_factory() as session:
            persisted = await SqlAlchemyRunRepository(session).get(
                tenant_id=tenant.id,
                run_id=run.id,
            )
            assert persisted is not None and persisted.status is RunStatus.FAILED
            assert await SqlAlchemyRunCommandRepository(session).is_processed(malformed_command.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_valid_unowned_settlement_fences_takeover_until_commit(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _run_fixture()
    tenant, _, _, run, command_record = fixture
    now = datetime.now(UTC)
    try:
        await _persist_fixture(session_factory, fixture)
        ownership_locked = asyncio.Event()
        allow_settlement = asyncio.Event()
        original_claim = SqlAlchemyRuntimeOwnershipRepository.claim

        async def claim_then_pause(
            repository: SqlAlchemyRuntimeOwnershipRepository,
            *,
            run_id: UUID,
            tenant_id: UUID,
            owner_id: str,
            now: datetime,
            lease_duration: timedelta,
        ) -> RuntimeOwnership:
            claimed = await original_claim(
                repository,
                run_id=run_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                now=now,
                lease_duration=lease_duration,
            )
            if owner_id.startswith("dead-letter:"):
                ownership_locked.set()
                await allow_settlement.wait()
            return claimed

        monkeypatch.setattr(
            SqlAlchemyRuntimeOwnershipRepository,
            "claim",
            claim_then_pause,
        )
        delivery = RunQueueDelivery(
            delivery_id=f"postgres-valid-{uuid4()}",
            message=RunQueueMessage(
                command_id=command_record.id,
                run_id=run.id,
                tenant_id=tenant.id,
                action="message",
            ),
        )
        settlement_task = asyncio.create_task(
            RunDeadLetterService(session_factory=session_factory).record_failure(
                delivery,
                attempts=5,
                error_type="delivery_processing_failed",
            )
        )
        await ownership_locked.wait()

        async def take_over() -> RuntimeOwnership:
            async with session_factory() as session:
                claimed = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
                    run_id=run.id,
                    tenant_id=tenant.id,
                    owner_id="replacement-worker",
                    now=now + timedelta(seconds=31),
                    lease_duration=timedelta(seconds=30),
                )
                await session.commit()
                return claimed

        takeover_task = asyncio.create_task(take_over())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(takeover_task), timeout=0.05)
        allow_settlement.set()
        dead_letter = await settlement_task
        replacement = await takeover_task

        assert dead_letter.settled_run_id == run.id
        assert replacement.owner_id == "replacement-worker"
        async with session_factory() as session:
            persisted = await SqlAlchemyRunRepository(session).get(
                tenant_id=tenant.id,
                run_id=run.id,
            )
            assert persisted is not None and persisted.status is RunStatus.FAILED
            assert await SqlAlchemyRunCommandRepository(session).is_processed(command_record.id)
            current = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
            assert current == replacement
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_cancel_committed_first_is_not_overwritten_by_dead_letter(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _run_fixture()
    tenant, _, _, run, command_record = fixture
    cancel_session = session_factory()
    try:
        await _persist_fixture(session_factory, fixture)
        runs = SqlAlchemyRunRepository(cancel_session)
        locked = await runs.get_for_update(tenant_id=tenant.id, run_id=run.id)
        assert locked is not None
        await runs.update(locked.transition_to(RunStatus.CANCELLED))
        events = SqlAlchemyRunEventRepository(cancel_session)
        await events.append(
            PlatformEvent.create(
                tenant_id=tenant.id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=1,
                event_type=EventType.RUN_CANCELLED,
                payload={"source": "api"},
            )
        )
        delivery = RunQueueDelivery(
            delivery_id=f"postgres-cancel-first-{uuid4()}",
            message=RunQueueMessage(
                command_id=command_record.id,
                run_id=run.id,
                tenant_id=tenant.id,
                action="message",
            ),
        )
        settlement_task = asyncio.create_task(
            RunDeadLetterService(session_factory=session_factory).record_failure(
                delivery,
                attempts=5,
                error_type="delivery_processing_failed",
            )
        )
        await asyncio.sleep(0.05)
        assert not settlement_task.done()
        await cancel_session.commit()
        dead_letter = await settlement_task

        assert dead_letter.settled_run_id == run.id
        async with session_factory() as session:
            persisted = await SqlAlchemyRunRepository(session).get(
                tenant_id=tenant.id,
                run_id=run.id,
            )
            assert persisted is not None and persisted.status is RunStatus.CANCELLED
            assert await SqlAlchemyRunCommandRepository(session).is_processed(command_record.id)
            persisted_events = await SqlAlchemyRunEventRepository(session).list(
                run_id=run.id,
                after_sequence=0,
            )
            assert [event.type for event in persisted_events] == [EventType.RUN_CANCELLED]
    finally:
        await cancel_session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_dead_letter_committed_first_prevents_late_cancel_overwrite(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _run_fixture()
    tenant, _, _, run, command_record = fixture
    try:
        await _persist_fixture(session_factory, fixture)
        run_locked = asyncio.Event()
        allow_settlement = asyncio.Event()
        original_get_for_update = SqlAlchemyRunRepository.get_for_update

        async def get_for_update_then_pause(
            repository: SqlAlchemyRunRepository,
            *,
            tenant_id: UUID,
            run_id: UUID,
        ) -> Run | None:
            locked = await original_get_for_update(
                repository,
                tenant_id=tenant_id,
                run_id=run_id,
            )
            if not run_locked.is_set():
                run_locked.set()
                await allow_settlement.wait()
            return locked

        monkeypatch.setattr(
            SqlAlchemyRunRepository,
            "get_for_update",
            get_for_update_then_pause,
        )
        delivery = RunQueueDelivery(
            delivery_id=f"postgres-dead-letter-first-{uuid4()}",
            message=RunQueueMessage(
                command_id=command_record.id,
                run_id=run.id,
                tenant_id=tenant.id,
                action="message",
            ),
        )
        settlement_task = asyncio.create_task(
            RunDeadLetterService(session_factory=session_factory).record_failure(
                delivery,
                attempts=5,
                error_type="delivery_processing_failed",
            )
        )
        await run_locked.wait()

        async def cancel_if_active() -> RunStatus:
            async with session_factory() as session:
                repository = SqlAlchemyRunRepository(session)
                current = await repository.get_for_update(
                    tenant_id=tenant.id,
                    run_id=run.id,
                )
                assert current is not None
                if current.status not in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    current = current.transition_to(RunStatus.CANCELLED)
                    await repository.update(current)
                await session.commit()
                return current.status

        cancel_task = asyncio.create_task(cancel_if_active())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(cancel_task), timeout=0.05)
        allow_settlement.set()
        dead_letter = await settlement_task
        cancel_status = await cancel_task

        assert dead_letter.settled_run_id == run.id
        assert cancel_status is RunStatus.FAILED
        async with session_factory() as session:
            persisted = await SqlAlchemyRunRepository(session).get(
                tenant_id=tenant.id,
                run_id=run.id,
            )
            assert persisted is not None and persisted.status is RunStatus.FAILED
            assert await SqlAlchemyRunCommandRepository(session).is_processed(command_record.id)
            assert await SqlAlchemyRunEventRepository(session).list(
                run_id=run.id,
                after_sequence=0,
            ) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True], ids=["valid", "malformed"])
async def test_postgres_pending_dead_letter_retries_same_delivery_after_owner_release(
    migrated_postgres_url: str,
    malformed: bool,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = _run_fixture()
    tenant, _, _, run, command_record = fixture
    delivery_id = f"postgres-pending-{malformed}-{uuid4()}"
    try:
        await _persist_fixture(session_factory, fixture)
        async with session_factory() as session:
            live_owner = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
                run_id=run.id,
                tenant_id=tenant.id,
                owner_id="foreign-live-worker",
                now=datetime.now(UTC),
                lease_duration=timedelta(seconds=30),
            )
            await session.commit()
        service = RunDeadLetterService(session_factory=session_factory)
        delivery = RunQueueDelivery(
            delivery_id=delivery_id,
            message=RunQueueMessage(
                command_id=command_record.id,
                run_id=run.id,
                tenant_id=tenant.id,
                action="message",
            ),
        )
        raw_fields = {
            "command_id": str(command_record.id),
            "run_id": str(run.id),
            "tenant_id": str(tenant.id),
            "action": "message",
            "payload": "not-json",
        }
        with pytest.raises(DeadLetterSettlementPending) as captured:
            if malformed:
                await service.record_malformed(
                    delivery_id=delivery_id,
                    attempts=5,
                    error_type="malformed_queue_message",
                    raw_fields=raw_fields,
                )
            else:
                await service.record_failure(
                    delivery,
                    attempts=5,
                    error_type="delivery_processing_failed",
                )
        assert captured.value.dead_letter.settled_run_id is None
        async with session_factory() as session:
            records = list((await session.execute(select(RunDeadLetterRecord))).scalars())
            matching = [record for record in records if record.original_delivery_id == delivery_id]
            assert len(matching) == 1 and matching[0].settled_run_id is None
            assert not await SqlAlchemyRunCommandRepository(session).is_processed(
                command_record.id
            )
            await SqlAlchemyRuntimeOwnershipRepository(session).release(
                run_id=run.id,
                owner_id=live_owner.owner_id or "",
                epoch=live_owner.epoch,
            )
            await session.commit()

        if malformed:
            settled = await service.record_malformed(
                delivery_id=delivery_id,
                attempts=6,
                error_type="malformed_queue_message",
                raw_fields=raw_fields,
            )
        else:
            settled = await service.record_failure(
                delivery,
                attempts=6,
                error_type="delivery_processing_failed",
            )
        assert settled.id == captured.value.dead_letter.id
        assert settled.settled_run_id == run.id
        async with session_factory() as session:
            assert await SqlAlchemyRunCommandRepository(session).is_processed(command_record.id)
    finally:
        await engine.dispose()
