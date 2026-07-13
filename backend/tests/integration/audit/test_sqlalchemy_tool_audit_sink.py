from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.audit import (
    SqlAlchemyToolAuditSink,
    ToolAuditPersistenceError,
    ToolAuditRecord,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.tool_gateway.errors import ToolInvocationClaimRejected
from agent_platform.platform.tool_gateway.models import (
    ArgumentSummary,
    AuditEventType,
    ToolAuditEvent,
)
from agent_platform.platform.tools.entities import ToolRiskLevel


def _event(*, secret: str = "never-persist-this") -> ToolAuditEvent:
    del secret
    return ToolAuditEvent(
        event_type=AuditEventType.COMPLETED,
        occurred_at=datetime(2026, 7, 13, 2, 30, tzinfo=UTC),
        tenant_id=uuid4(),
        run_id=uuid4(),
        employee_id=uuid4(),
        user_id=uuid4(),
        tool_id=uuid4(),
        tool_name="crm.update",
        risk=ToolRiskLevel.WRITE,
        argument_summary=ArgumentSummary(
            keys=("customer_id", "password"),
            sha256="a" * 64,
            size_bytes=128,
        ),
        reason="completed",
        succeeded=True,
    )


@pytest.mark.asyncio
async def test_sink_commits_safe_tool_audit_fields_in_an_independent_transaction() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    sink = SqlAlchemyToolAuditSink(session_factory)
    event = _event()

    await sink.emit(event)

    async with session_factory() as session:
        record = (await session.execute(select(ToolAuditRecord))).scalar_one()
    assert record.event_type == "tool.completed"
    assert record.occurred_at.replace(tzinfo=UTC) == event.occurred_at
    assert record.tenant_id == event.tenant_id
    assert record.run_id == event.run_id
    assert record.employee_id == event.employee_id
    assert record.user_id == event.user_id
    assert record.tool_id == event.tool_id
    assert record.tool_name == "crm.update"
    assert record.risk == "write"
    assert record.argument_keys == ["customer_id", "password"]
    assert record.argument_sha256 == "a" * 64
    assert record.argument_size_bytes == 128
    assert record.reason == "completed"
    assert record.succeeded is True
    assert "never-persist-this" not in repr(record.__dict__)
    assert "arguments" not in ToolAuditRecord.__table__.columns
    assert "credentials" not in ToolAuditRecord.__table__.columns
    assert "result" not in ToolAuditRecord.__table__.columns
    await engine.dispose()


@pytest.mark.asyncio
async def test_each_emit_commits_without_an_external_session_commit() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    sink = SqlAlchemyToolAuditSink(session_factory)

    await sink.emit(_event())
    await sink.emit(_event())

    async with session_factory() as session:
        records = (await session.execute(select(ToolAuditRecord))).scalars().all()
    assert len(records) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_started_audit_atomically_rejects_a_preceding_cancel_intent() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    cancel = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        await session.commit()
    started = replace(
        _event(),
        event_type=AuditEventType.STARTED,
        tenant_id=run.tenant_id,
        run_id=run.id,
        employee_id=run.employee_id,
        user_id=run.created_by,
        invocation_id=uuid4(),
    )

    with pytest.raises(ToolInvocationClaimRejected):
        await SqlAlchemyToolAuditSink(session_factory).emit(started)

    async with session_factory() as session:
        records = (await session.execute(select(ToolAuditRecord))).scalars().all()
    assert records == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_sink_exposes_only_a_sanitized_error_message_on_database_failure() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    sink = SqlAlchemyToolAuditSink(session_factory)
    secret = "database-secret-must-not-leak"

    with pytest.raises(ToolAuditPersistenceError) as caught:
        await sink.emit(_event(secret=secret))

    assert str(caught.value) == "Tool audit persistence failed"
    assert secret not in repr(caught.value)
    await engine.dispose()
