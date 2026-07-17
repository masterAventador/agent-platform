"""SqlAlchemyApprovalRepository 单元测试（内存 SQLite）。

覆盖：幂等创建（同 request_key 返回原记录）、租户隔离、CAS 只一人生效、
待办/历史过滤与分页、按 run 结算 pending、过期清扫候选查询。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.approvals import (
    SqlAlchemyApprovalRepository,
)
from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    value = async_sessionmaker(engine, expire_on_commit=False)
    yield value
    await engine.dispose()


def _approval(
    *,
    tenant_id: UUID | None = None,
    request_key: str | None = None,
    assignee_id: UUID | None = None,
    requested_by: UUID | None = None,
    run_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    return Approval.create(
        tenant_id=tenant_id or uuid4(),
        source=ApprovalSource.TOOL_RISK,
        approval_type="tool.invocation",
        risk_level="external",
        requested_by=requested_by or uuid4(),
        request_key=request_key or f"tool:{uuid4()}",
        context={"tool_name": "send_email"},
        run_id=run_id or uuid4(),
        invocation_id=uuid4(),
        employee_id=uuid4(),
        assignee_id=assignee_id,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_add_idempotent_returns_existing_record_for_same_request_key(factory) -> None:
    tenant_id = uuid4()
    original = _approval(tenant_id=tenant_id, request_key="tool:run:approval")
    duplicate = _approval(tenant_id=tenant_id, request_key="tool:run:approval")

    async with factory() as session:
        first = await SqlAlchemyApprovalRepository(session).add_idempotent(original)
        await session.commit()
    async with factory() as session:
        second = await SqlAlchemyApprovalRepository(session).add_idempotent(duplicate)
        await session.commit()

    assert first.id == original.id
    assert second.id == original.id


@pytest.mark.asyncio
async def test_same_request_key_in_other_tenant_creates_separate_record(factory) -> None:
    first = _approval(request_key="tool:shared")
    second = _approval(request_key="tool:shared")

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        await repository.add_idempotent(first)
        created = await repository.add_idempotent(second)
        await session.commit()

    assert created.id == second.id


@pytest.mark.asyncio
async def test_get_is_tenant_isolated(factory) -> None:
    approval = _approval()
    async with factory() as session:
        await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
        await session.commit()

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        assert await repository.get(
            tenant_id=approval.tenant_id, approval_id=approval.id
        ) is not None
        assert await repository.get(tenant_id=uuid4(), approval_id=approval.id) is None


@pytest.mark.asyncio
async def test_update_with_cas_only_one_concurrent_decision_wins(factory) -> None:
    approval = _approval()
    async with factory() as session:
        await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
        await session.commit()

    first_decision = approval.approve(decided_by=uuid4())
    second_decision = approval.reject(decided_by=uuid4(), reason="并发拒绝")

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        assert await repository.update_with_cas(
            first_decision, expected_revision=approval.revision
        ) is True
        await session.commit()
    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        assert await repository.update_with_cas(
            second_decision, expected_revision=approval.revision
        ) is False
        await session.commit()

    async with factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=approval.tenant_id, approval_id=approval.id
        )
    assert stored is not None
    assert stored.status is ApprovalStatus.APPROVED
    assert stored.revision == approval.revision + 1


@pytest.mark.asyncio
async def test_list_filters_statuses_assignee_and_paginates(factory) -> None:
    tenant_id = uuid4()
    assignee = uuid4()
    pending_unassigned = _approval(tenant_id=tenant_id)
    pending_assigned = _approval(tenant_id=tenant_id, assignee_id=assignee)
    decided = _approval(tenant_id=tenant_id)
    other_tenant = _approval()

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        for item in (pending_unassigned, pending_assigned, other_tenant):
            await repository.add_idempotent(item)
        await repository.add_idempotent(decided)
        await repository.update_with_cas(
            decided.approve(decided_by=uuid4()), expected_revision=decided.revision
        )
        await session.commit()

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        pending, pending_total = await repository.list(
            tenant_id=tenant_id,
            statuses=(ApprovalStatus.PENDING,),
            limit=10,
            offset=0,
        )
        history, history_total = await repository.list(
            tenant_id=tenant_id,
            statuses=(
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.WITHDRAWN,
                ApprovalStatus.TRANSFERRED,
            ),
            limit=10,
            offset=0,
        )
        assigned, assigned_total = await repository.list(
            tenant_id=tenant_id,
            statuses=(ApprovalStatus.PENDING,),
            assignee_id=assignee,
            limit=10,
            offset=0,
        )
        paged, paged_total = await repository.list(
            tenant_id=tenant_id,
            statuses=(ApprovalStatus.PENDING,),
            limit=1,
            offset=1,
        )

    assert pending_total == 2
    assert {item.id for item in pending} == {pending_unassigned.id, pending_assigned.id}
    assert history_total == 1
    assert history[0].id == decided.id
    assert assigned_total == 1
    assert assigned[0].id == pending_assigned.id
    assert paged_total == 2
    assert len(paged) == 1


@pytest.mark.asyncio
async def test_list_visible_to_limits_non_manager_users(factory) -> None:
    tenant_id = uuid4()
    member = uuid4()
    unassigned = _approval(tenant_id=tenant_id)
    assigned_to_member = _approval(tenant_id=tenant_id, assignee_id=member)
    assigned_to_other = _approval(tenant_id=tenant_id, assignee_id=uuid4())
    requested_by_member = _approval(tenant_id=tenant_id, requested_by=member)

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        for item in (unassigned, assigned_to_member, assigned_to_other, requested_by_member):
            await repository.add_idempotent(item)
        await session.commit()

    async with factory() as session:
        visible, total = await SqlAlchemyApprovalRepository(session).list(
            tenant_id=tenant_id,
            statuses=(ApprovalStatus.PENDING,),
            visible_to=member,
            include_unassigned=False,
            limit=10,
            offset=0,
        )

    assert total == 2
    assert {item.id for item in visible} == {assigned_to_member.id, requested_by_member.id}


@pytest.mark.asyncio
async def test_get_active_for_invocation_returns_pending_chain_head(factory) -> None:
    approval = _approval()
    transferred, child = approval.transfer(decided_by=uuid4(), assignee_id=uuid4())

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        await repository.add_idempotent(approval)
        await repository.update_with_cas(transferred, expected_revision=approval.revision)
        await repository.add_idempotent(child)
        await session.commit()

    async with factory() as session:
        assert approval.run_id is not None and approval.invocation_id is not None
        active = await SqlAlchemyApprovalRepository(session).get_active_for_invocation(
            tenant_id=approval.tenant_id,
            run_id=approval.run_id,
            invocation_id=approval.invocation_id,
        )

    assert active is not None
    assert active.id == child.id


@pytest.mark.asyncio
async def test_list_pending_for_run_and_overdue_candidates(factory) -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    tenant_id = uuid4()
    overdue = _approval(
        tenant_id=tenant_id, run_id=run_id, expires_at=now - timedelta(seconds=5)
    )
    fresh = _approval(tenant_id=tenant_id, run_id=run_id, expires_at=now + timedelta(hours=1))
    other_run = _approval(tenant_id=tenant_id)

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        for item in (overdue, fresh, other_run):
            await repository.add_idempotent(item)
        await session.commit()

    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        for_run = await repository.list_pending_for_run(tenant_id=tenant_id, run_id=run_id)
        candidates = await repository.list_overdue_pending(now=now, limit=10)

    assert {item.id for item in for_run} == {overdue.id, fresh.id}
    assert [item.id for item in candidates] == [overdue.id]
