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

BACKEND_ROOT = Path(__file__).parents[3]


class DeleteOnlyProvider:
    name = "concurrency-test"

    def __init__(self) -> None:
        self.deleted: list[tuple[str, UUID]] = []

    async def delete(self, *, sandbox_id: str, lease_id: UUID) -> None:
        self.deleted.append((sandbox_id, lease_id))


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
async def test_postgres_renew_and_janitor_claim_cannot_lost_update(
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
        manager.renew(
            lease_id=lease.id,
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
    renew_outcome, cleanup_outcome = outcomes
    if isinstance(renew_outcome, SandboxLease):
        assert persisted.status is SandboxLeaseStatus.ACTIVE
        assert persisted.expires_at == now + timedelta(seconds=2, hours=1)
        assert provider.deleted == []
        assert cleanup_outcome == []
    else:
        assert isinstance(renew_outcome, SandboxLeaseUnavailable)
        assert persisted.status is SandboxLeaseStatus.EXPIRED
        assert provider.deleted == [(lease.sandbox_id, lease.id)]
        assert cleanup_outcome == [lease.id]
    await engine.dispose()
