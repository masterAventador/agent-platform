import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnershipLost,
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.queue.dead_letters import (
    DeadLetterSettlementPending,
    RunDeadLetterService,
)
from agent_platform.infrastructure.queue.dispatcher import RunCommandDispatcher
from agent_platform.infrastructure.queue.redis_streams import (
    RedisRunQueue,
    RunQueueDelivery,
    RunQueueMessage,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_dead_letter_transaction_fails_run_and_processes_original_command(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=7,
        created_by=uuid4(),
        input_data={"task": "durable"},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()

    service = RunDeadLetterService(session_factory=session_factory)
    record = await service.record_failure(
        RunQueueDelivery(
            delivery_id="1-0",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        attempts=5,
        error_type="delivery_processing_failed",
    )

    assert record.original_command_id == command.id
    assert record.attempts == 5
    assert record.error_type == "delivery_processing_failed"
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None
        assert persisted.status is RunStatus.FAILED
        assert persisted.error_code == "delivery_attempts_exhausted"
        assert persisted.error_message is None
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_creates_new_run_and_start_command(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=3,
        created_by=uuid4(),
        input_data={"task": "clone me"},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
        payload={"message": "old control"},
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    service = RunDeadLetterService(session_factory=session_factory)
    dead_letter = await service.record_failure(
        RunQueueDelivery(
            delivery_id="2-0",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="message",
                payload=command.payload,
            ),
        ),
        attempts=5,
        error_type="delivery_processing_failed",
    )

    first = await service.replay(tenant_id=run.tenant_id, dead_letter_id=dead_letter.id)
    second = await service.replay(tenant_id=run.tenant_id, dead_letter_id=dead_letter.id)

    assert first == second
    assert first.run_id != run.id
    assert first.command_id != command.id
    async with session_factory() as session:
        cloned = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=first.run_id,
        )
        assert cloned is not None
        assert cloned.status is RunStatus.QUEUED
        assert cloned.employee_id == run.employee_id
        assert cloned.employee_version == run.employee_version
        assert cloned.input_data == run.input_data
        pending = await SqlAlchemyRunCommandRepository(session).pending(limit=100)
        replayed = [item for item in pending if item.id == first.command_id]
        assert len(replayed) == 1
        assert replayed[0].action is RunCommandAction.START


@pytest.mark.asyncio
async def test_malformed_dead_letter_stores_only_bounded_metadata_and_cannot_replay(
    session_factory,
) -> None:
    service = RunDeadLetterService(session_factory=session_factory)

    record = await service.record_malformed(
        delivery_id="3-0",
        attempts=5,
        error_type="malformed_queue_message",
        raw_fields={"payload": "secret-value", "unexpected": "x" * 5000},
    )

    assert record.original_command_id is None
    assert record.raw_fields_summary["known_field_keys"] == ["payload"]
    assert record.raw_fields_summary["unknown_fields"][0]["length"] == 10
    assert len(record.raw_fields_summary["unknown_fields"][0]["sha256"]) == 64
    assert record.raw_fields_summary["field_count"] == 2
    assert record.raw_fields_summary["total_bytes"] == 5029
    assert len(record.raw_fields_summary["sha256"]) == 64
    assert "secret-value" not in repr(record.raw_fields_summary)
    with pytest.raises(LookupError):
        await service.replay(tenant_id=uuid4(), dead_letter_id=record.id)


@pytest.mark.asyncio
async def test_malformed_with_verified_ids_fails_and_processes_original_but_stays_admin_only(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="current-worker",
            now=datetime.now(UTC),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()
    service = RunDeadLetterService(session_factory=session_factory)

    record = await service.record_malformed(
        delivery_id="verified-malformed",
        attempts=5,
        error_type="malformed_queue_message",
        raw_fields={
            "command_id": str(command.id),
            "run_id": str(run.id),
            "tenant_id": str(run.tenant_id),
            "action": "start",
            "payload": "not-json-and-must-not-persist",
        },
        ownerships=(ownership,),
    )

    assert record.is_malformed is True
    assert record.original_command_id == command.id
    assert await service.list(tenant_id=run.tenant_id, limit=10) == []
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None
        assert persisted.status is RunStatus.FAILED
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
        released = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert released is not None and released.owner_id is None


@pytest.mark.asyncio
async def test_malformed_cannot_settle_run_owned_by_another_live_worker(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        live_owner = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="new-worker",
            now=datetime.now(UTC),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()

    service = RunDeadLetterService(session_factory=session_factory)
    raw_fields = {
        "command_id": str(command.id),
        "run_id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "action": "message",
        "payload": "not-json",
    }
    with pytest.raises(DeadLetterSettlementPending) as captured:
        await service.record_malformed(
            delivery_id="foreign-live-owner",
            attempts=5,
            error_type="malformed_queue_message",
            raw_fields=raw_fields,
        )
    record = captured.value.dead_letter

    assert record.settled_run_id is None
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership == live_owner
        await SqlAlchemyRuntimeOwnershipRepository(session).release(
            run_id=run.id,
            owner_id=live_owner.owner_id or "",
            epoch=live_owner.epoch,
        )
        await session.commit()

    settled = await service.record_malformed(
        delivery_id="foreign-live-owner",
        attempts=6,
        error_type="malformed_queue_message",
        raw_fields=raw_fields,
    )
    assert settled.id == record.id
    assert settled.settled_run_id == run.id


@pytest.mark.asyncio
async def test_valid_dead_letter_without_ownership_cannot_settle_live_foreign_owner(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        live_owner = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="replacement-worker",
            now=datetime.now(UTC),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()

    service = RunDeadLetterService(session_factory=session_factory)
    delivery = RunQueueDelivery(
        delivery_id="valid-foreign-live-owner",
        message=RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="message",
        ),
    )
    with pytest.raises(DeadLetterSettlementPending) as captured:
        await service.record_failure(
            delivery,
            attempts=5,
            error_type="delivery_processing_failed",
        )
    record = captured.value.dead_letter

    assert record.settled_run_id is None
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership == live_owner
        await SqlAlchemyRuntimeOwnershipRepository(session).release(
            run_id=run.id,
            owner_id=live_owner.owner_id or "",
            epoch=live_owner.epoch,
        )
        await session.commit()

    settled = await service.record_failure(
        delivery,
        attempts=6,
        error_type="delivery_processing_failed",
    )
    assert settled.id == record.id
    assert settled.settled_run_id == run.id


@pytest.mark.asyncio
async def test_malformed_safely_settles_run_after_previous_owner_expires(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.MESSAGE,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    expired_at = datetime.now(UTC) - timedelta(seconds=2)
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="expired-worker",
            now=expired_at,
            lease_duration=timedelta(seconds=1),
        )
        await session.commit()

    record = await RunDeadLetterService(session_factory=session_factory).record_malformed(
        delivery_id="expired-owner",
        attempts=5,
        error_type="malformed_queue_message",
        raw_fields={
            "command_id": str(command.id),
            "run_id": str(run.id),
            "tenant_id": str(run.tenant_id),
            "action": "message",
            "payload": "not-json",
        },
    )

    assert record.settled_run_id == run.id
    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.FAILED
        assert await SqlAlchemyRunCommandRepository(session).is_processed(command.id)
        ownership = await SqlAlchemyRuntimeOwnershipRepository(session).get(run_id=run.id)
        assert ownership is not None and ownership.owner_id is None


@pytest.mark.asyncio
async def test_malformed_cross_spliced_ids_cannot_mutate_another_run(session_factory) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    run_a = Run.create(
        tenant_id=tenant_a,
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    run_b = Run.create(
        tenant_id=tenant_b,
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command_a = RunCommand.create(
        run_id=run_a.id,
        tenant_id=run_a.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        await runs.add(run_a)
        await runs.add(run_b)
        await SqlAlchemyRunCommandRepository(session).add(command_a)
        await session.commit()

    await RunDeadLetterService(session_factory=session_factory).record_malformed(
        delivery_id="cross-spliced-malformed",
        attempts=5,
        error_type="malformed_queue_message",
        raw_fields={
            "command_id": str(command_a.id),
            "run_id": str(run_b.id),
            "tenant_id": str(run_b.tenant_id),
            "action": "start",
            "payload": "not-json",
        },
    )

    async with session_factory() as session:
        runs = SqlAlchemyRunRepository(session)
        persisted_a = await runs.get(tenant_id=tenant_a, run_id=run_a.id)
        persisted_b = await runs.get(tenant_id=tenant_b, run_id=run_b.id)
        assert persisted_a is not None and persisted_a.status is RunStatus.RUNNING
        assert persisted_b is not None and persisted_b.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(command_a.id)


@pytest.mark.asyncio
async def test_list_and_replay_are_tenant_scoped(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    service = RunDeadLetterService(session_factory=session_factory)
    record = await service.record_failure(
        RunQueueDelivery(
            delivery_id="tenant-scope",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        attempts=5,
        error_type="delivery_processing_failed",
    )
    another_tenant = uuid4()

    assert await service.list(tenant_id=another_tenant, limit=10) == []
    with pytest.raises(LookupError):
        await service.replay(tenant_id=another_tenant, dead_letter_id=record.id)


@pytest.mark.asyncio
async def test_redis_mirror_is_compensatable_and_contains_no_command_payload(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"secret": "must-not-be-mirrored"},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
        payload={"secret": "must-not-be-mirrored"},
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    service = RunDeadLetterService(session_factory=session_factory)
    await service.record_failure(
        RunQueueDelivery(
            delivery_id="mirror-1",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        attempts=5,
        error_type="delivery_processing_failed",
    )

    class Publisher:
        def __init__(self) -> None:
            self.records = []
            self.fail = True

        async def publish_dead_letter(self, record) -> None:
            self.records.append(record)
            if self.fail:
                raise RuntimeError("redis unavailable")

    publisher = Publisher()
    assert await service.reconcile_mirrors(publisher=publisher, limit=10) == 0
    publisher.fail = False
    assert await service.reconcile_mirrors(publisher=publisher, limit=10) == 1
    assert await service.reconcile_mirrors(publisher=publisher, limit=10) == 0
    assert "must-not-be-mirrored" not in repr(publisher.records)


@pytest.mark.asyncio
async def test_stale_runtime_owner_cannot_dead_letter_after_takeover(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    now = datetime.now(UTC)
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        ownerships = SqlAlchemyRuntimeOwnershipRepository(session)
        stale = await ownerships.claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="old-worker",
            now=now,
            lease_duration=timedelta(seconds=1),
        )
        await session.commit()
    async with session_factory() as session:
        await SqlAlchemyRuntimeOwnershipRepository(session).claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="new-worker",
            now=now + timedelta(seconds=2),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()

    service = RunDeadLetterService(session_factory=session_factory)
    with pytest.raises(RuntimeOwnershipLost):
        await service.record_failure(
            RunQueueDelivery(
                delivery_id="stale-owner",
                message=RunQueueMessage(
                    command_id=command.id,
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    action="start",
                ),
            ),
            attempts=5,
            error_type="delivery_processing_failed",
            ownership=stale,
        )

    async with session_factory() as session:
        persisted = await SqlAlchemyRunRepository(session).get(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert persisted is not None and persisted.status is RunStatus.RUNNING
        assert not await SqlAlchemyRunCommandRepository(session).is_processed(command.id)


@pytest.mark.asyncio
async def test_committed_dead_letter_is_idempotent_after_ownership_release(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        ownerships = SqlAlchemyRuntimeOwnershipRepository(session)
        ownership = await ownerships.claim(
            run_id=run.id,
            tenant_id=run.tenant_id,
            owner_id="worker",
            now=datetime.now(UTC),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()
    delivery = RunQueueDelivery(
        delivery_id="ack-crash",
        message=RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="start",
        ),
    )
    service = RunDeadLetterService(session_factory=session_factory)
    first = await service.record_failure(
        delivery,
        attempts=5,
        error_type="delivery_processing_failed",
        ownership=ownership,
    )
    async with session_factory() as session:
        await SqlAlchemyRuntimeOwnershipRepository(session).release(
            run_id=run.id,
            owner_id=ownership.owner_id or "",
            epoch=ownership.epoch,
        )
        await session.commit()

    second = await service.record_failure(
        delivery,
        attempts=6,
        error_type="delivery_processing_failed",
        ownership=ownership,
    )

    assert second.id == first.id


@pytest.mark.asyncio
async def test_concurrent_dead_letter_inserts_are_idempotent(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    delivery = RunQueueDelivery(
        delivery_id="concurrent-record",
        message=RunQueueMessage(
            command_id=command.id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            action="start",
        ),
    )
    service = RunDeadLetterService(session_factory=session_factory)

    first, second = await asyncio.gather(
        service.record_failure(
            delivery,
            attempts=5,
            error_type="delivery_processing_failed",
        ),
        service.record_failure(
            delivery,
            attempts=5,
            error_type="delivery_processing_failed",
        ),
    )

    assert first.id == second.id


@pytest.mark.asyncio
async def test_concurrent_malformed_dead_letter_inserts_are_idempotent(session_factory) -> None:
    service = RunDeadLetterService(session_factory=session_factory)
    arguments = {
        "delivery_id": "concurrent-malformed",
        "attempts": 5,
        "error_type": "malformed_queue_message",
        "raw_fields": {"payload": "secret"},
    }

    first, second = await asyncio.gather(
        service.record_malformed(**arguments),
        service.record_malformed(**arguments),
    )

    assert first.id == second.id


@pytest.mark.asyncio
async def test_delivery_identity_is_scoped_by_source_stream(session_factory) -> None:
    service = RunDeadLetterService(session_factory=session_factory)

    first = await service.record_malformed(
        source_stream="runs-before-recreate",
        delivery_id="1-0",
        attempts=5,
        error_type="malformed_queue_message",
        raw_fields={"payload": "invalid"},
    )
    second = await service.record_malformed(
        source_stream="runs-after-recreate",
        delivery_id="1-0",
        attempts=5,
        error_type="malformed_queue_message",
        raw_fields={"payload": "invalid"},
    )

    assert first.id != second.id


@pytest.mark.asyncio
async def test_concurrent_reconcilers_produce_one_visible_mirror(session_factory) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    service = RunDeadLetterService(session_factory=session_factory)
    await service.record_failure(
        RunQueueDelivery(
            delivery_id="concurrent-mirror",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        attempts=5,
        error_type="delivery_processing_failed",
    )

    class IdempotentPublisher:
        def __init__(self) -> None:
            self.visible: set = set()

        async def publish_dead_letter(self, record) -> None:
            await asyncio.sleep(0)
            self.visible.add(record.id)

    publisher = IdempotentPublisher()
    await asyncio.gather(
        service.reconcile_mirrors(publisher=publisher, limit=10),
        service.reconcile_mirrors(publisher=publisher, limit=10),
    )

    assert len(publisher.visible) == 1


@pytest.mark.asyncio
async def test_real_redis_mirror_is_idempotent_and_replay_enters_dispatch_stream(
    session_factory,
) -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("需要 TEST_REDIS_URL 才运行真实 Redis DLQ 镜像测试")
    redis = Redis.from_url(redis_url, decode_responses=True)
    stream_name = f"test:dlq-replay:{uuid4()}"
    dlq_name = f"test:dlq-replay:{uuid4()}:dlq"
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=2,
        created_by=uuid4(),
        input_data={"task": "safe replay"},
    ).transition_to(RunStatus.RUNNING)
    command = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.START,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunRepository,
    )

    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(command)
        await session.commit()
    service = RunDeadLetterService(session_factory=session_factory)
    dead_letter = await service.record_failure(
        RunQueueDelivery(
            delivery_id="real-mirror",
            message=RunQueueMessage(
                command_id=command.id,
                run_id=run.id,
                tenant_id=run.tenant_id,
                action="start",
            ),
        ),
        attempts=5,
        error_type="delivery_processing_failed",
    )
    queue = RedisRunQueue(
        redis,
        stream_name=stream_name,
        group_name="test-workers",
        dead_letter_stream_name=dlq_name,
    )
    try:
        await queue.setup()
        await asyncio.gather(
            service.reconcile_mirrors(publisher=queue, limit=10),
            service.reconcile_mirrors(publisher=queue, limit=10),
        )
        mirrored = await redis.xrange(dlq_name, min="-", max="+")
        assert len(mirrored) == 1
        assert mirrored[0][1]["dead_letter_id"] == str(dead_letter.id)
        assert "payload" not in mirrored[0][1]

        replayed = await service.replay(
            tenant_id=run.tenant_id,
            dead_letter_id=dead_letter.id,
        )
        assert (
            await RunCommandDispatcher(
                session_factory=session_factory,
                queue=queue,
            ).dispatch_pending()
            == 1
        )
        delivery = await queue.dequeue(consumer_name="replay-worker", block_ms=100)
        assert delivery is not None
        assert delivery.message.command_id == replayed.command_id
        assert delivery.message.run_id == replayed.run_id
        assert delivery.message.action == "start"
        await queue.acknowledge(delivery.delivery_id)
    finally:
        await redis.delete(stream_name, dlq_name, f"{dlq_name}:dedupe")
        await redis.aclose()
