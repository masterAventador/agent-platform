"""真实 PostgreSQL 下的审批并发/CAS 门禁（C13）。

SQLite 的 SELECT FOR UPDATE 是 no-op、无真实 MVCC，单元层「并发」不算数。
本门禁用两个真实独立 asyncpg session 制造真并发，验证：
① 并发 approve/reject 只一方生效，run 只入队一条决策命令；
② 同 (tenant_id, request_key) 并发插入只留一条（唯一约束）；
③ 后台清扫 expire 与惰性决策真并发，CAS 只一方生效、终态一致。

需 TEST_DATABASE_URL 才运行；缺失时 skip（不假绿）。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.approvals import (
    ApprovalRecord,
    SqlAlchemyApprovalRepository,
    create_approval_service,
    expire_overdue_approvals,
)
from agent_platform.infrastructure.database.repositories.audit import (
    AuditChainStateRecord,
    AuditEventRecord,
)
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.employees import EmployeeRecord
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunEventRecord,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)
from agent_platform.platform.approvals.errors import (
    ApprovalConcurrencyConflict,
    ApprovalExpired,
    ApprovalNotPending,
)
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.tenants.memberships import TenantRole

BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 审批并发门禁")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    return database_url


@pytest_asyncio.fixture
async def session_factory(
    migrated_postgres_url: str,
) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(migrated_postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    # 每个用例后清理本用例写入的表数据（保留 schema），避免跨用例串染
    async with factory() as session:
        for table in (
            RunCommandRecord,
            RunEventRecord,
            ApprovalRecord,
            AuditEventRecord,
            AuditChainStateRecord,
        ):
            await session.execute(delete(table))
        # runs / users / tenants 有 FK，按依赖顺序清
        from agent_platform.infrastructure.database.repositories.runs import RunRecord

        await session.execute(delete(RunRecord))
        await session.execute(delete(EmployeeRecord))
        await session.execute(delete(UserRecord))
        await session.execute(delete(TenantRecord))
        await session.commit()
    await engine.dispose()


async def _seed_tenant_user(session_factory: async_sessionmaker) -> tuple[UUID, UUID, UUID]:
    """建满足真实 FK 的最小 tenant + user + published employee，返回三者 id。"""
    tenant_id = uuid4()
    user_id = uuid4()
    employee_id = uuid4()
    async with session_factory() as session:
        session.add(
            TenantRecord(
                id=tenant_id,
                name="并发门禁租户",
                slug=f"concurrency-{tenant_id.hex}",
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            UserRecord(
                id=user_id,
                email=f"approver-{user_id.hex}@example.com",
                password_hash="x",
                created_at=datetime.now(UTC),
            )
        )
        # 先 flush 父行，保证 employee 的 tenant/user FK 已存在
        await session.flush()
        session.add(
            EmployeeRecord(
                id=employee_id,
                tenant_id=tenant_id,
                created_by=user_id,
                name="并发门禁员工",
                role_description="验证并发",
                visibility="tenant",
                runtime_type="autonomous",
                system_prompt="x",
                model_settings={},
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                capabilities={},
                skill_ids=[],
                tool_ids=[],
                knowledge_base_ids=[],
                knowledge_retrieval={},
                approval_policy={},
                release_strategy={},
                status="published",
                published_version=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return tenant_id, user_id, employee_id


async def _seed_waiting_run(
    session_factory: async_sessionmaker,
    *,
    tenant_id: UUID,
    created_by: UUID,
    employee_id: UUID,
) -> Run:
    run = Run.create(
        tenant_id=tenant_id,
        employee_id=employee_id,
        employee_version=1,
        created_by=created_by,
        input_data={},
    )
    run = run.transition_to(RunStatus.RUNNING).transition_to(RunStatus.WAITING_FOR_APPROVAL)
    async with session_factory() as session:
        await SqlAlchemyRunRepository(session).add(run)
        await session.commit()
    return run


async def _seed_pending_approval(
    session_factory: async_sessionmaker,
    *,
    tenant_id: UUID,
    run: Run,
    requested_by: UUID,
    request_key: str | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    approval = Approval.create(
        tenant_id=tenant_id,
        source=ApprovalSource.TOOL_RISK,
        approval_type="tool.invocation",
        risk_level="external",
        requested_by=requested_by,
        request_key=request_key or f"tool:{run.id}:{uuid4()}",
        context={"tool_name": "send_email"},
        run_id=run.id,
        invocation_id=uuid4(),
        employee_id=run.employee_id,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    async with session_factory() as session:
        await SqlAlchemyApprovalRepository(session).add_idempotent(approval)
        await session.commit()
    return approval


async def _run_command_actions(
    session_factory: async_sessionmaker, run_id: UUID
) -> list[str]:
    async with session_factory() as session:
        result = await session.execute(
            select(RunCommandRecord.action).where(RunCommandRecord.run_id == run_id)
        )
        return list(result.scalars())


@pytest.mark.asyncio
async def test_concurrent_decisions_only_one_wins_and_one_command(session_factory) -> None:
    tenant_id, approver, employee_id = await _seed_tenant_user(session_factory)
    run = await _seed_waiting_run(
        session_factory, tenant_id=tenant_id, created_by=approver, employee_id=employee_id
    )
    approval = await _seed_pending_approval(
        session_factory, tenant_id=tenant_id, run=run, requested_by=approver
    )

    async def decide(action: str) -> object:
        async with session_factory() as session:
            service = create_approval_service(session)
            try:
                if action == "approve":
                    result = await service.approve(
                        tenant_id=tenant_id,
                        approval_id=approval.id,
                        actor_id=approver,
                        actor_role=TenantRole.ADMIN,
                    )
                else:
                    result = await service.reject(
                        tenant_id=tenant_id,
                        approval_id=approval.id,
                        actor_id=approver,
                        actor_role=TenantRole.ADMIN,
                        reason="并发拒绝",
                    )
                await session.commit()
                return result
            except (ApprovalConcurrencyConflict, ApprovalNotPending) as error:
                await session.rollback()
                return error

    results = await asyncio.gather(decide("approve"), decide("reject"))

    successes = [r for r in results if isinstance(r, Approval)]
    conflicts = [
        r for r in results if isinstance(r, (ApprovalConcurrencyConflict, ApprovalNotPending))
    ]
    assert len(successes) == 1, f"应只一方生效，实际 {results}"
    assert len(conflicts) == 1

    async with session_factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=tenant_id, approval_id=approval.id
        )
    assert stored is not None
    assert stored.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
    assert stored.revision == 2

    # run 只入队恰好一条决策命令
    actions = await _run_command_actions(session_factory, run.id)
    assert len(actions) == 1
    assert actions[0] in {"approve", "reject"}
    assert actions[0] == stored.status.value.replace("approved", "approve").replace(
        "rejected", "reject"
    )


@pytest.mark.asyncio
async def test_concurrent_same_request_key_insert_keeps_one(session_factory) -> None:
    tenant_id, approver, employee_id = await _seed_tenant_user(session_factory)
    run = await _seed_waiting_run(
        session_factory, tenant_id=tenant_id, created_by=approver, employee_id=employee_id
    )
    request_key = f"tool:{run.id}:shared-invocation"

    def _build() -> Approval:
        return Approval.create(
            tenant_id=tenant_id,
            source=ApprovalSource.TOOL_RISK,
            approval_type="tool.invocation",
            risk_level="external",
            requested_by=approver,
            request_key=request_key,
            context={"tool_name": "send_email"},
            run_id=run.id,
            invocation_id=uuid4(),
            employee_id=run.employee_id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def insert() -> UUID:
        async with session_factory() as session:
            created = await SqlAlchemyApprovalRepository(session).add_idempotent(_build())
            await session.commit()
            return created.id

    ids = await asyncio.gather(insert(), insert())

    # 幂等：两并发插入返回同一条（唯一约束只留一行）
    async with session_factory() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(ApprovalRecord)
                .where(
                    ApprovalRecord.tenant_id == tenant_id,
                    ApprovalRecord.request_key == request_key,
                )
            )
        ).scalar_one()
    assert total == 1
    assert ids[0] == ids[1]


@pytest.mark.asyncio
async def test_concurrent_expiry_sweep_and_decision_are_cas_consistent(
    session_factory,
) -> None:
    tenant_id, approver, employee_id = await _seed_tenant_user(session_factory)
    run = await _seed_waiting_run(
        session_factory, tenant_id=tenant_id, created_by=approver, employee_id=employee_id
    )
    approval = await _seed_pending_approval(
        session_factory,
        tenant_id=tenant_id,
        run=run,
        requested_by=approver,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    async def sweep() -> object:
        try:
            return await expire_overdue_approvals(
                session_factory, now=datetime.now(UTC), limit=100
            )
        except Exception as error:  # noqa: BLE001 - 测试记录异常对象
            return error

    async def lazy_decide() -> object:
        async with session_factory() as session:
            try:
                result = await create_approval_service(session).approve(
                    tenant_id=tenant_id,
                    approval_id=approval.id,
                    actor_id=approver,
                    actor_role=TenantRole.ADMIN,
                )
                await session.commit()
                return result
            except (ApprovalExpired, ApprovalConcurrencyConflict, ApprovalNotPending) as error:
                await session.rollback()
                return error

    await asyncio.gather(sweep(), lazy_decide())

    # 终态一致：过期决策不允许通过；记录必为 expired，且只入队一条 reject 命令
    async with session_factory() as session:
        stored = await SqlAlchemyApprovalRepository(session).get(
            tenant_id=tenant_id, approval_id=approval.id
        )
    assert stored is not None
    assert stored.status is ApprovalStatus.EXPIRED
    assert stored.revision == 2

    actions = await _run_command_actions(session_factory, run.id)
    assert actions == ["reject"]
