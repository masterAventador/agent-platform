"""ApprovalService 单元测试：RBAC、状态机、CAS、超时、撤回、转交与 run 命令联动。"""

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
    create_approval_service,
)
from agent_platform.infrastructure.database.repositories.audit import AuditEventRecord
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
from agent_platform.platform.approvals.errors import (
    ApprovalExpired,
    ApprovalNotPending,
    ApprovalPermissionDenied,
    ApprovalReasonRequired,
    ApprovalRunNotActionable,
)
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.tenants.memberships import TenantRole


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    value = async_sessionmaker(engine, expire_on_commit=False)
    yield value
    await engine.dispose()


async def _seed_waiting_run(session: AsyncSession, *, tenant_id: UUID, created_by: UUID) -> Run:
    run = Run.create(
        tenant_id=tenant_id,
        employee_id=uuid4(),
        employee_version=1,
        created_by=created_by,
        input_data={"task": "需要审批的任务"},
    )
    run = run.transition_to(RunStatus.RUNNING).transition_to(RunStatus.WAITING_FOR_APPROVAL)
    await SqlAlchemyRunRepository(session).add(run)
    return run


async def _seed_pending_approval(
    session: AsyncSession,
    *,
    run: Run,
    assignee_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    approval = Approval.create(
        tenant_id=run.tenant_id,
        source=ApprovalSource.TOOL_RISK,
        approval_type="tool.invocation",
        risk_level="external",
        requested_by=run.created_by,
        request_key=f"tool:{run.id}:{uuid4()}",
        context={"tool_name": "send_email", "arguments": {"to": "a@b.c"}},
        run_id=run.id,
        invocation_id=uuid4(),
        employee_id=run.employee_id,
        assignee_id=assignee_id,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
    return approval


async def _run_commands(session: AsyncSession, run_id: UUID) -> list[RunCommandRecord]:
    result = await session.execute(
        select(RunCommandRecord).where(RunCommandRecord.run_id == run_id)
    )
    return list(result.scalars())


@pytest.mark.asyncio
async def test_approve_marks_record_and_enqueues_run_command_event_audit(factory) -> None:
    tenant_id = uuid4()
    approver = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        decided = await create_approval_service(session).approve(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=approver,
            actor_role=TenantRole.ADMIN,
            reason="允许执行",
        )
        await session.commit()

    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == approver
    async with factory() as session:
        commands = await _run_commands(session, run.id)
        assert [command.action for command in commands] == ["approve"]
        assert commands[0].payload["approval_id"] == str(approval.invocation_id)
        events = await SqlAlchemyRunEventRepository(session).list(
            run_id=run.id, after_sequence=0
        )
        assert [event.type.value for event in events] == ["run.progress"]
        audit_actions = (
            (
                await session.execute(
                    select(AuditEventRecord.action).where(
                        AuditEventRecord.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "approval.approved" in audit_actions


@pytest.mark.asyncio
async def test_reject_requires_reason_and_records_it(factory) -> None:
    tenant_id = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        service = create_approval_service(session)
        with pytest.raises(ApprovalReasonRequired):
            await service.reject(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.ADMIN,
                reason="   ",
            )
        rejected = await service.reject(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=uuid4(),
            actor_role=TenantRole.OWNER,
            reason="外部发送未获许可",
        )
        await session.commit()

    assert rejected.status is ApprovalStatus.REJECTED
    assert rejected.decision_reason == "外部发送未获许可"
    async with factory() as session:
        commands = await _run_commands(session, run.id)
    assert [command.action for command in commands] == ["reject"]


@pytest.mark.asyncio
async def test_member_role_cannot_decide_unassigned_approval(factory) -> None:
    tenant_id = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        with pytest.raises(ApprovalPermissionDenied):
            await create_approval_service(session).approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.MEMBER,
            )


@pytest.mark.asyncio
async def test_assigned_approval_only_assignee_can_decide(factory) -> None:
    tenant_id = uuid4()
    assignee = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run, assignee_id=assignee)
        await session.commit()

    async with factory() as session:
        service = create_approval_service(session)
        with pytest.raises(ApprovalPermissionDenied):
            await service.approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.OWNER,
            )
        decided = await service.approve(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=assignee,
            actor_role=TenantRole.ADMIN,
        )
        await session.commit()

    assert decided.status is ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_decide_after_expiry_marks_expired_and_rejects_run(factory) -> None:
    tenant_id = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(
            session,
            run=run,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(ApprovalExpired):
            await create_approval_service(session).approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.ADMIN,
            )
        await session.commit()

    async with factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=tenant_id, approval_id=approval.id
        )
        commands = await _run_commands(session, run.id)
    assert stored is not None and stored.status is ApprovalStatus.EXPIRED
    # 过期结算同时驱动 run 拒绝，任务不会永远悬挂在等待审批
    assert [command.action for command in commands] == ["reject"]


@pytest.mark.asyncio
async def test_decide_on_withdrawn_approval_raises_not_pending(factory) -> None:
    tenant_id = uuid4()
    requester = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=requester)
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        service = create_approval_service(session)
        await service.withdraw(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=requester,
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(ApprovalNotPending):
            await create_approval_service(session).approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.ADMIN,
            )


@pytest.mark.asyncio
async def test_withdraw_only_requester_and_rejects_run(factory) -> None:
    tenant_id = uuid4()
    requester = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=requester)
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        service = create_approval_service(session)
        with pytest.raises(ApprovalPermissionDenied):
            await service.withdraw(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
            )
        withdrawn = await service.withdraw(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=requester,
            reason="任务不需要了",
        )
        await session.commit()

    assert withdrawn.status is ApprovalStatus.WITHDRAWN
    async with factory() as session:
        commands = await _run_commands(session, run.id)
    assert [command.action for command in commands] == ["reject"]


@pytest.mark.asyncio
async def test_decide_when_run_already_terminal_raises(factory) -> None:
    tenant_id = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        runs = SqlAlchemyRunRepository(session)
        await runs.update(run.transition_to(RunStatus.CANCELLED))
        await session.commit()

    async with factory() as session:
        with pytest.raises(ApprovalRunNotActionable):
            await create_approval_service(session).approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.ADMIN,
            )


@pytest.mark.asyncio
async def test_decision_after_terminal_hits_not_pending_guard(factory) -> None:
    """顺序场景：一方决策已提交（终态）后，另一方再决策命中终态守卫 ApprovalNotPending。

    这不是真并发（SQLite 的 SELECT FOR UPDATE 是 no-op，无真实 MVCC/CAS）；真正的
    多 session 并发只一方生效由第 2 项真实 PostgreSQL 门禁验证
    （tests/integration/approvals/test_postgres_approval_concurrency.py）。
    """
    tenant_id = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    # 第一方决策完整提交（进入终态）
    async with factory() as session:
        await create_approval_service(session).approve(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=uuid4(),
            actor_role=TenantRole.ADMIN,
        )
        await session.commit()

    # 第二方再决策：重读见终态，命中终态守卫（非 CAS 冲突）
    async with factory() as session:
        with pytest.raises(ApprovalNotPending):
            await create_approval_service(session).reject(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.ADMIN,
                reason="重复决策",
            )

    async with factory() as session:
        commands = await _run_commands(session, run.id)
    assert [command.action for command in commands] == ["approve"]


@pytest.mark.asyncio
async def test_repeated_decision_with_same_decision_key_is_idempotent(factory) -> None:
    tenant_id = uuid4()
    approver = uuid4()
    decision_key = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        first = await create_approval_service(session).approve(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=approver,
            actor_role=TenantRole.ADMIN,
            decision_key=decision_key,
        )
        await session.commit()
    async with factory() as session:
        replay = await create_approval_service(session).approve(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=approver,
            actor_role=TenantRole.ADMIN,
            decision_key=decision_key,
        )
        await session.commit()

    assert replay.id == first.id
    assert replay.status is ApprovalStatus.APPROVED
    assert replay.revision == first.revision
    async with factory() as session:
        commands = await _run_commands(session, run.id)
    # 幂等重放不再追加第二条 run 命令
    assert [command.action for command in commands] == ["approve"]


@pytest.mark.asyncio
async def test_transfer_creates_chain_and_validates_target_role(factory) -> None:
    tenant_id = uuid4()
    new_assignee = uuid4()
    async with factory() as session:
        run = await _seed_waiting_run(session, tenant_id=tenant_id, created_by=uuid4())
        approval = await _seed_pending_approval(session, run=run)
        await session.commit()

    async with factory() as session:
        service = create_approval_service(session)
        with pytest.raises(ApprovalPermissionDenied):
            await service.transfer(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.ADMIN,
                assignee_id=new_assignee,
                assignee_role=TenantRole.MEMBER,
            )
        child = await service.transfer(
            tenant_id=tenant_id,
            approval_id=approval.id,
            actor_id=uuid4(),
            actor_role=TenantRole.ADMIN,
            assignee_id=new_assignee,
            assignee_role=TenantRole.ADMIN,
        )
        await session.commit()

    assert child.assignee_id == new_assignee
    assert child.transferred_from_id == approval.id
    async with factory() as session:
        repository = SqlAlchemyApprovalRepository(session)
        original = await repository.get(tenant_id=tenant_id, approval_id=approval.id)
        assert original is not None
        assert original.status is ApprovalStatus.TRANSFERRED
        assert original.transferred_to_id == child.id
        # 转交后原记录不可再决策；新记录可由被转交人决策
        service = create_approval_service(session)
        with pytest.raises(ApprovalNotPending):
            await service.approve(
                tenant_id=tenant_id,
                approval_id=approval.id,
                actor_id=uuid4(),
                actor_role=TenantRole.OWNER,
            )
        decided = await service.approve(
            tenant_id=tenant_id,
            approval_id=child.id,
            actor_id=new_assignee,
            actor_role=TenantRole.ADMIN,
        )
        await session.commit()
    assert decided.status is ApprovalStatus.APPROVED
