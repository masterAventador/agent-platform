from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.audit import (
    SqlAlchemyToolAuditSink,
    ToolAuditRecord,
)
from agent_platform.infrastructure.database.repositories.auth import (
    SqlAlchemyUserRepository,
)
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
)
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.tool_gateway import (
    ArgumentSummary,
    AuditEventType,
    ToolAuditEvent,
)
from agent_platform.platform.tool_gateway.errors import ToolInvocationClaimRejected
from agent_platform.platform.users.entities import User

BACKEND_ROOT = Path(__file__).parents[3]


class BarrierToolAuditSink(SqlAlchemyToolAuditSink):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        claim_locked: asyncio.Event,
        release_claim: asyncio.Event,
    ) -> None:
        super().__init__(session_factory)
        self._claim_locked = claim_locked
        self._release_claim = release_claim

    async def _after_started_claim_locked(self) -> None:
        self._claim_locked.set()
        await self._release_claim.wait()


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 工具声明并发测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


async def _create_running_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> Run:
    tenant = Tenant.create(name="Tool claim", slug=f"tool-claim-{uuid4().hex}")
    user = User.create(email=f"{uuid4().hex}@example.com", password_hash="hash")
    employee = Employee.create(
        tenant_id=tenant.id,
        created_by=user.id,
        draft=EmployeeDraft(
            name="Tool claim employee",
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
    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        await SqlAlchemyUserRepository(session).add(user)
        await SqlAlchemyEmployeeRepository(session).add(employee)
        await SqlAlchemyRunRepository(session).add(run)
        await session.commit()
    return run


def _started_event(run: Run) -> ToolAuditEvent:
    return ToolAuditEvent(
        event_type=AuditEventType.STARTED,
        occurred_at=datetime.now(UTC),
        tenant_id=run.tenant_id,
        run_id=run.id,
        employee_id=run.employee_id,
        user_id=run.created_by,
        tool_id=uuid4(),
        tool_name="crm.update",
        risk=None,
        argument_summary=ArgumentSummary(keys=("customer_id",), sha256="a" * 64, size_bytes=1),
        invocation_id=uuid4(),
    )


async def _commit_cancel(
    session_factory: async_sessionmaker[AsyncSession],
    run: Run,
    *,
    attempting_lock: asyncio.Event | None = None,
    lock_acquired: asyncio.Event | None = None,
    release_lock: asyncio.Event | None = None,
) -> RunCommand:
    cancel = RunCommand.create(
        run_id=run.id,
        tenant_id=run.tenant_id,
        action=RunCommandAction.CANCEL,
    )
    async with session_factory() as session:
        if attempting_lock is not None:
            attempting_lock.set()
        current = await SqlAlchemyRunRepository(session).get_for_update(
            tenant_id=run.tenant_id,
            run_id=run.id,
        )
        assert current is not None
        await SqlAlchemyRunCommandRepository(session).add(cancel)
        if lock_acquired is not None:
            lock_acquired.set()
        if release_lock is not None:
            await release_lock.wait()
        await session.commit()
    return cancel


async def _started_records(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
) -> list[ToolAuditRecord]:
    async with session_factory() as session:
        result = await session.execute(
            select(ToolAuditRecord).where(
                ToolAuditRecord.run_id == run_id,
                ToolAuditRecord.event_type == AuditEventType.STARTED.value,
            )
        )
        return list(result.scalars())


@pytest.mark.asyncio
async def test_postgres_claim_first_commits_started_before_waiting_cancel(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    run = await _create_running_run(session_factory)
    claim_locked = asyncio.Event()
    release_claim = asyncio.Event()
    sink = BarrierToolAuditSink(
        session_factory,
        claim_locked=claim_locked,
        release_claim=release_claim,
    )
    claim_task = asyncio.create_task(sink.emit(_started_event(run)))
    await asyncio.wait_for(claim_locked.wait(), timeout=1)
    cancel_attempting = asyncio.Event()
    cancel_task = asyncio.create_task(
        _commit_cancel(
            session_factory,
            run,
            attempting_lock=cancel_attempting,
        )
    )
    await asyncio.wait_for(cancel_attempting.wait(), timeout=1)
    await asyncio.sleep(0.05)
    assert not cancel_task.done()

    release_claim.set()
    await asyncio.wait_for(claim_task, timeout=1)
    cancel = await asyncio.wait_for(cancel_task, timeout=1)

    assert len(await _started_records(session_factory, run.id)) == 1
    async with session_factory() as session:
        assert await SqlAlchemyRunCommandRepository(session).is_processed(cancel.id) is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_cancel_first_rejects_waiting_started_claim(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    run = await _create_running_run(session_factory)
    cancel_locked = asyncio.Event()
    release_cancel = asyncio.Event()
    cancel_task = asyncio.create_task(
        _commit_cancel(
            session_factory,
            run,
            lock_acquired=cancel_locked,
            release_lock=release_cancel,
        )
    )
    await asyncio.wait_for(cancel_locked.wait(), timeout=1)
    claim_task = asyncio.create_task(
        SqlAlchemyToolAuditSink(session_factory).emit(_started_event(run))
    )
    await asyncio.sleep(0.05)
    assert not claim_task.done()

    release_cancel.set()
    await asyncio.wait_for(cancel_task, timeout=1)
    with pytest.raises(ToolInvocationClaimRejected):
        await asyncio.wait_for(claim_task, timeout=1)

    assert await _started_records(session_factory, run.id) == []
    await engine.dispose()
