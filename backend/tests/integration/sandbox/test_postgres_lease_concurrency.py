from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.auth import SqlAlchemyUserRepository
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
)
from agent_platform.infrastructure.database.repositories.runs import SqlAlchemyRunRepository
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnership,
    RuntimeOwnershipBusy,
    RuntimeOwnershipLost,
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.database.repositories.sandbox import (
    SqlAlchemySandboxLeaseRepository,
    SqlAlchemySandboxLeaseUnitOfWorkFactory,
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
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.users.entities import User
from agent_platform.sandbox.entities import SandboxLease, SandboxLeaseStatus, SandboxScope
from agent_platform.sandbox.errors import SandboxLeaseUnavailable
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.ports import ProviderSandbox, RunExecutionEnvironment

BACKEND_ROOT = Path(__file__).parents[3]


class DeleteOnlyProvider:
    name = "concurrency-test"

    def __init__(self) -> None:
        self.deleted: list[tuple[str, UUID]] = []
        self.reconnected: list[tuple[str, UUID]] = []

    async def delete(self, *, sandbox_id: str, lease_id: UUID, sandbox_epoch: int) -> None:
        del sandbox_epoch
        self.deleted.append((sandbox_id, lease_id))

    async def reconnect(
        self, *, sandbox_id: str, lease_id: UUID, sandbox_epoch: int
    ) -> ProviderSandbox:
        self.reconnected.append((sandbox_id, lease_id))
        return ProviderSandbox(
            sandbox_id=sandbox_id,
            workspace=object(),
            backend=object(),
            sandbox_epoch=sandbox_epoch,
        )


class UnusedValidator:
    @staticmethod
    def validate(backend: object) -> None:
        del backend


@pytest.fixture(scope="module")
def migrated_postgres_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 并发测试")
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.mark.asyncio
async def test_postgres_recovery_reconnect_and_janitor_claim_cannot_lost_update(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = Tenant.create(name="Sandbox concurrency", slug=f"sandbox-race-{uuid4().hex}")
    user = User.create(email=f"{uuid4().hex}@example.com", password_hash="hash")
    draft = EmployeeDraft(
        name="Concurrency employee",
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
    )
    employee = Employee.create(tenant_id=tenant.id, created_by=user.id, draft=draft)
    run = Run.create(
        tenant_id=tenant.id,
        employee_id=employee.id,
        employee_version=1,
        created_by=user.id,
        input_data={},
    )
    now = datetime(2026, 7, 13, tzinfo=UTC)
    scope = SandboxScope(
        tenant_id=tenant.id,
        user_id=user.id,
        run_id=run.id,
        thread_id=run.thread_id,
    )
    lease = SandboxLease.create(
        scope=scope,
        provider=DeleteOnlyProvider.name,
        ttl=timedelta(seconds=1),
        now=now,
    ).activate("postgres-concurrency-box", now=now)
    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        await SqlAlchemyUserRepository(session).add(user)
        await SqlAlchemyEmployeeRepository(session).add(employee)
        await SqlAlchemyRunRepository(session).add(run)
        await SqlAlchemySandboxLeaseRepository(session).add(lease)
        await session.commit()
    provider = DeleteOnlyProvider()
    manager = SandboxManager(
        unit_of_work_factory=SqlAlchemySandboxLeaseUnitOfWorkFactory(session_factory),
        providers={provider.name: provider},
        provider_name=provider.name,
        backend_validator=UnusedValidator(),
    )

    outcomes = await asyncio.gather(
        manager.reconnect_active(
            scope=scope,
            ttl=timedelta(hours=1),
            now=now + timedelta(seconds=2),
        ),
        manager.cleanup_expired(now=now + timedelta(seconds=2), limit=10),
        return_exceptions=True,
    )

    async with session_factory() as session:
        persisted = await SqlAlchemySandboxLeaseRepository(session).get(
            tenant_id=tenant.id, lease_id=lease.id
        )
    assert persisted is not None
    assert persisted.status in {SandboxLeaseStatus.ACTIVE, SandboxLeaseStatus.EXPIRED}
    reconnect_outcome, cleanup_outcome = outcomes
    if isinstance(reconnect_outcome, RunExecutionEnvironment):
        assert persisted.status is SandboxLeaseStatus.ACTIVE
        assert persisted.expires_at == now + timedelta(seconds=2, hours=1)
        assert provider.deleted == []
        assert provider.reconnected == [(lease.sandbox_id, lease.id)]
        assert cleanup_outcome == []
    else:
        assert isinstance(reconnect_outcome, SandboxLeaseUnavailable)
        assert persisted.status is SandboxLeaseStatus.EXPIRED
        assert provider.deleted == [(lease.sandbox_id, lease.id)]
        assert cleanup_outcome == [lease.id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_runtime_ownership_concurrent_claim_and_aba_fencing(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant = Tenant.create(name="Runtime ownership", slug=f"ownership-{uuid4().hex}")
    user = User.create(email=f"{uuid4().hex}@example.com", password_hash="hash")
    employee = Employee.create(
        tenant_id=tenant.id,
        created_by=user.id,
        draft=EmployeeDraft(
            name="Ownership employee",
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
    )
    async with session_factory() as session:
        await SqlAlchemyTenantRepository(session).add(tenant)
        await SqlAlchemyUserRepository(session).add(user)
        await SqlAlchemyEmployeeRepository(session).add(employee)
        await SqlAlchemyRunRepository(session).add(run)
        await session.commit()
    now = datetime.now(UTC)

    async def claim(owner_id: str, at: datetime) -> RuntimeOwnership:
        async with session_factory() as session:
            ownership = await SqlAlchemyRuntimeOwnershipRepository(session).claim(
                run_id=run.id,
                tenant_id=tenant.id,
                owner_id=owner_id,
                now=at,
                lease_duration=timedelta(seconds=10),
            )
            await session.commit()
            return ownership

    first_claims = await asyncio.gather(
        claim("worker-a", now),
        claim("worker-b", now),
        return_exceptions=True,
    )
    winners = [value for value in first_claims if isinstance(value, RuntimeOwnership)]
    losers = [value for value in first_claims if isinstance(value, RuntimeOwnershipBusy)]
    assert len(winners) == 1
    assert len(losers) == 1
    first = winners[0]
    ownership_checked = asyncio.Event()
    release_guard = asyncio.Event()

    async def hold_old_owner_guard() -> None:
        async with session_factory() as session:
            await SqlAlchemyRuntimeOwnershipRepository(session).assert_owned(
                run_id=run.id,
                owner_id=first.owner_id or "",
                epoch=first.epoch,
                now=now + timedelta(seconds=5),
            )
            ownership_checked.set()
            await release_guard.wait()
            await session.commit()

    guard_task = asyncio.create_task(hold_old_owner_guard())
    await ownership_checked.wait()
    takeover_task = asyncio.create_task(claim("worker-c", now + timedelta(seconds=11)))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(takeover_task), timeout=0.05)
    release_guard.set()
    await guard_task
    second = await takeover_task
    async with session_factory() as session:
        repository = SqlAlchemyRuntimeOwnershipRepository(session)
        with pytest.raises(RuntimeOwnershipLost):
            await repository.assert_owned(
                run_id=run.id,
                owner_id=first.owner_id or "",
                epoch=first.epoch,
                now=now + timedelta(seconds=11),
            )
        assert await repository.release(
            run_id=run.id,
            owner_id=second.owner_id or "",
            epoch=second.epoch,
            now=now + timedelta(seconds=12),
        )
        await session.commit()
    third = await claim("worker-d", now + timedelta(seconds=13))
    assert third.epoch == second.epoch + 1
    async with session_factory() as session:
        with pytest.raises(RuntimeOwnershipLost):
            await SqlAlchemyRuntimeOwnershipRepository(session).assert_owned(
                run_id=run.id,
                owner_id=second.owner_id or "",
                epoch=second.epoch,
                now=now + timedelta(seconds=13),
            )
    await engine.dispose()
