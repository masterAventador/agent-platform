"""审批超时后台清扫单元测试。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.approvals import (
    SqlAlchemyApprovalRepository,
    expire_overdue_approvals,
)
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)
from agent_platform.platform.runs.entities import Run, RunStatus


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    value = async_sessionmaker(engine, expire_on_commit=False)
    yield value
    await engine.dispose()


async def _seed(
    session: AsyncSession,
    *,
    expires_delta: timedelta,
    run_status: RunStatus = RunStatus.WAITING_FOR_APPROVAL,
) -> tuple[Run, Approval]:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    run = run.transition_to(RunStatus.RUNNING)
    if run_status is not RunStatus.RUNNING:
        run = run.transition_to(run_status)
    await SqlAlchemyRunRepository(session).add(run)
    approval = Approval.create(
        tenant_id=run.tenant_id,
        source=ApprovalSource.TOOL_RISK,
        approval_type="tool.invocation",
        risk_level="external",
        requested_by=run.created_by,
        request_key=f"tool:{run.id}:{uuid4()}",
        context={"tool_name": "send_email"},
        run_id=run.id,
        invocation_id=uuid4(),
        employee_id=run.employee_id,
        expires_at=datetime.now(UTC) + expires_delta,
    )
    await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
    return run, approval


async def _commands(session: AsyncSession, run_id: UUID) -> list[str]:
    result = await session.execute(
        select(RunCommandRecord.action).where(RunCommandRecord.run_id == run_id)
    )
    return list(result.scalars())


@pytest.mark.asyncio
async def test_sweep_expires_overdue_and_rejects_waiting_run(factory) -> None:
    async with factory() as session:
        run, approval = await _seed(session, expires_delta=timedelta(seconds=-5))
        fresh_run, fresh = await _seed(session, expires_delta=timedelta(hours=1))
        await session.commit()

    result = await expire_overdue_approvals(factory, now=datetime.now(UTC), limit=100)

    assert result.expired == 1
    assert result.failed == 0
    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        stored = await repository.get(tenant_id=approval.tenant_id, approval_id=approval.id)
        untouched = await repository.get(tenant_id=fresh.tenant_id, approval_id=fresh.id)
        assert stored is not None and stored.status is ApprovalStatus.EXPIRED
        assert untouched is not None and untouched.status is ApprovalStatus.PENDING
        assert await _commands(session, run.id) == ["reject"]
        assert await _commands(session, fresh_run.id) == []


@pytest.mark.asyncio
async def test_sweep_skips_run_command_when_run_no_longer_waiting(factory) -> None:
    async with factory() as session:
        run, approval = await _seed(
            session,
            expires_delta=timedelta(seconds=-5),
            run_status=RunStatus.RUNNING,
        )
        await session.commit()

    result = await expire_overdue_approvals(factory, now=datetime.now(UTC), limit=100)

    assert result.expired == 1
    async with factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=approval.tenant_id, approval_id=approval.id
        )
        assert stored is not None and stored.status is ApprovalStatus.EXPIRED
        assert await _commands(session, run.id) == []


@pytest.mark.asyncio
async def test_sweep_notification_failure_does_not_block_expiry(
    factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as session:
        run, approval = await _seed(session, expires_delta=timedelta(seconds=-5))
        await session.commit()

    async def fail_append(self, event) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(SqlAlchemyRunEventRepository, "append", fail_append)

    result = await expire_overdue_approvals(factory, now=datetime.now(UTC), limit=100)

    assert result.expired == 1
    assert result.failed == 0
    async with factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=approval.tenant_id, approval_id=approval.id
        )
        assert stored is not None and stored.status is ApprovalStatus.EXPIRED
        # run 拒绝命令仍然入队，通知失败只损失事件
        assert await _commands(session, run.id) == ["reject"]


@pytest.mark.asyncio
async def test_sweep_is_idempotent_across_repeated_runs(factory) -> None:
    async with factory() as session:
        run, _ = await _seed(session, expires_delta=timedelta(seconds=-5))
        await session.commit()

    first = await expire_overdue_approvals(factory, now=datetime.now(UTC), limit=100)
    second = await expire_overdue_approvals(factory, now=datetime.now(UTC), limit=100)

    assert first.expired == 1
    assert second.expired == 0
    async with factory() as session:
        assert await _commands(session, run.id) == ["reject"]
