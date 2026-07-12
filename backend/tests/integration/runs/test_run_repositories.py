from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.runs import (
    EventSequenceConflict,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.runs.events import EventType, PlatformEvent


@pytest_asyncio.fixture
async def run_repositories() -> AsyncIterator[
    tuple[SqlAlchemyRunRepository, SqlAlchemyRunEventRepository]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield SqlAlchemyRunRepository(session), SqlAlchemyRunEventRepository(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_and_events_are_persisted_in_sequence(
    run_repositories: tuple[SqlAlchemyRunRepository, SqlAlchemyRunEventRepository],
) -> None:
    runs, events = run_repositories
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={"topic": "event ordering"},
    )
    await runs.add(run)
    first = PlatformEvent.create(
        tenant_id=run.tenant_id,
        employee_id=run.employee_id,
        run_id=run.id,
        sequence=1,
        event_type=EventType.RUN_STARTED,
        payload={},
    )
    second = PlatformEvent.create(
        tenant_id=run.tenant_id,
        employee_id=run.employee_id,
        run_id=run.id,
        sequence=2,
        event_type=EventType.RUN_PROGRESS,
        payload={"percent": 50},
    )
    await events.append(first)
    await events.append(second)

    assert await runs.get(tenant_id=run.tenant_id, run_id=run.id) == run
    assert await events.list(run_id=run.id, after_sequence=0) == [first, second]
    assert await events.list(run_id=run.id, after_sequence=1) == [second]

    with pytest.raises(EventSequenceConflict):
        await events.append(second.model_copy(update={"event_id": uuid4()}))
