from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.sandbox import (
    SqlAlchemySandboxLeaseRepository,
    SqlAlchemySandboxLeaseUnitOfWorkFactory,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.tenants.entities import Tenant
from agent_platform.platform.users.entities import User
from agent_platform.sandbox.entities import SandboxLease, SandboxScope
from agent_platform.sandbox.errors import SandboxLeaseScopeConflict


@pytest_asyncio.fixture
async def repository() -> AsyncIterator[
    tuple[
        SqlAlchemySandboxLeaseRepository,
        AsyncSession,
        async_sessionmaker[AsyncSession],
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        tenant = Tenant.create(name="Sandbox tenant", slug=f"sandbox-{uuid4().hex}")
        user = User.create(email=f"{uuid4().hex}@example.com", password_hash="hash")
        from agent_platform.infrastructure.database.repositories.auth import UserRecord
        from agent_platform.infrastructure.database.repositories.runs import RunRecord
        from agent_platform.infrastructure.database.repositories.tenants import TenantRecord

        session.add(
            TenantRecord(
                id=tenant.id, name=tenant.name, slug=tenant.slug, created_at=tenant.created_at
            )
        )
        session.add(
            UserRecord(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                email_verified=user.email_verified,
                created_at=user.created_at,
            )
        )
        run = Run.create(
            tenant_id=tenant.id,
            employee_id=uuid4(),
            employee_version=1,
            created_by=user.id,
            input_data={},
        )
        session.add(
            RunRecord(
                id=run.id,
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                employee_version=run.employee_version,
                created_by=run.created_by,
                thread_id=run.thread_id,
                input_data=run.input_data,
                status=run.status.value,
                created_at=run.created_at,
                updated_at=run.updated_at,
                started_at=None,
                finished_at=None,
                error_code=None,
                error_message=None,
            )
        )
        # SQLite fixtures deliberately omit employee rows; foreign keys are disabled by default.
        await session.commit()
        yield SqlAlchemySandboxLeaseRepository(session), session, session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_round_trips_lease_and_filters_tenant(
    repository: tuple[
        SqlAlchemySandboxLeaseRepository,
        AsyncSession,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    leases, session, _ = repository
    from agent_platform.infrastructure.database.repositories.runs import RunRecord

    run = (await session.execute(select(RunRecord))).scalar_one()
    scope = SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=run.id,
        thread_id=run.thread_id,
    )
    lease = SandboxLease.create(scope=scope, provider="fake", ttl=timedelta(minutes=10)).activate(
        "box-1"
    )
    await leases.add(lease)

    assert await leases.get(tenant_id=scope.tenant_id, lease_id=lease.id) == lease
    assert await leases.get(tenant_id=uuid4(), lease_id=lease.id) is None
    assert await leases.get_by_scope(scope=scope, provider="fake") == lease


@pytest.mark.asyncio
async def test_repository_lists_all_expired_non_terminal_leases_with_or_without_sandbox_id(
    repository: tuple[
        SqlAlchemySandboxLeaseRepository,
        AsyncSession,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    leases, session, _ = repository
    from agent_platform.infrastructure.database.repositories.runs import RunRecord

    run = (await session.execute(select(RunRecord))).scalar_one()
    now = datetime(2026, 7, 13, tzinfo=UTC)
    scope = SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=run.id,
        thread_id=run.thread_id,
    )

    def lease_for(label: str) -> SandboxLease:
        return SandboxLease.create(
            scope=SandboxScope(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                run_id=scope.run_id,
                thread_id=f"thread-{label}",
            ),
            provider="fake",
            ttl=timedelta(seconds=1),
            now=now,
        )

    recoverable = [
        lease_for("provisioning-no-id"),
        lease_for("active-with-id").activate("box-active", now=now),
        lease_for("deleting-with-id").activate("box-deleting", now=now).begin_delete(now=now),
        lease_for("error-no-id").mark_error("acquire_failed", now=now),
        lease_for("error-with-id")
        .activate("box-error", now=now)
        .mark_error("delete_failed", now=now),
    ]
    terminal = [
        lease_for("deleted").mark_deleted(now=now),
        lease_for("expired").mark_expired(now=now),
    ]
    future = replace(
        lease_for("future"),
        expires_at=now + timedelta(hours=1),
    )
    for lease in [*recoverable, *terminal, future]:
        await leases.add(lease)

    result = await leases.list_expired(now=now + timedelta(seconds=2), limit=20)

    assert {lease.id for lease in result} == {lease.id for lease in recoverable}


@pytest.mark.asyncio
async def test_repository_rejects_duplicate_scope_without_breaking_outer_transaction(
    repository: tuple[
        SqlAlchemySandboxLeaseRepository,
        AsyncSession,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    leases, session, _ = repository
    from agent_platform.infrastructure.database.repositories.runs import RunRecord

    run = (await session.execute(select(RunRecord))).scalar_one()
    scope = SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=run.id,
        thread_id=run.thread_id,
    )
    first = SandboxLease.create(scope=scope, provider="fake", ttl=timedelta(minutes=10))
    duplicate = SandboxLease.create(scope=scope, provider="fake", ttl=timedelta(minutes=10))
    await leases.add(first)

    with pytest.raises(SandboxLeaseScopeConflict):
        await leases.add(duplicate)

    assert await leases.get_by_scope(scope=scope, provider="fake") == first


@pytest.mark.asyncio
async def test_repository_rejects_same_provider_sandbox_for_different_leases(
    repository: tuple[
        SqlAlchemySandboxLeaseRepository,
        AsyncSession,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    leases, session, _ = repository
    from agent_platform.infrastructure.database.repositories.runs import RunRecord

    run = (await session.execute(select(RunRecord))).scalar_one()
    first_scope = SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=run.id,
        thread_id=run.thread_id,
    )
    second_scope = SandboxScope(
        tenant_id=run.tenant_id,
        user_id=run.created_by,
        run_id=uuid4(),
        thread_id="another-thread",
    )
    first = SandboxLease.create(
        scope=first_scope, provider="fake", ttl=timedelta(minutes=10)
    ).activate("same-sandbox")
    duplicate = SandboxLease.create(
        scope=second_scope, provider="fake", ttl=timedelta(minutes=10)
    ).activate("same-sandbox")
    await leases.add(first)

    with pytest.raises(SandboxLeaseScopeConflict):
        await leases.add(duplicate)

    assert await leases.get(tenant_id=first.tenant_id, lease_id=first.id) == first


@pytest.mark.asyncio
async def test_sqlalchemy_unit_of_work_commit_is_visible_to_an_independent_session(
    repository: tuple[
        SqlAlchemySandboxLeaseRepository,
        AsyncSession,
        async_sessionmaker[AsyncSession],
    ],
) -> None:
    _, session, session_factory = repository
    from agent_platform.infrastructure.database.repositories.runs import RunRecord

    run = (await session.execute(select(RunRecord))).scalar_one()
    lease = SandboxLease.create(
        scope=SandboxScope(
            tenant_id=run.tenant_id,
            user_id=run.created_by,
            run_id=run.id,
            thread_id=run.thread_id,
        ),
        provider="fake",
        ttl=timedelta(minutes=10),
    )
    unit_of_work_factory = SqlAlchemySandboxLeaseUnitOfWorkFactory(session_factory)

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.leases.add(lease)
        await unit_of_work.commit()

    async with session_factory() as independent_session:
        independent_repository = SqlAlchemySandboxLeaseRepository(independent_session)
        persisted = await independent_repository.get(tenant_id=lease.tenant_id, lease_id=lease.id)
    assert persisted == lease
