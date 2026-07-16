from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.entitlements import (
    SqlAlchemyCapabilityEntitlementRepository,
)
from agent_platform.platform.entitlements.entities import EntitlementStatus

_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    database_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'entitlements.db'}")
    load_database_models()
    async with database_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database_engine
    await database_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_grant_creates_active_entitlement(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    actor = uuid4()
    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        granted = await repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=actor,
            source="manual",
            expires_at=None,
            now=_NOW,
        )
        await session.commit()

    assert granted.status is EntitlementStatus.ACTIVE
    assert granted.tenant_id == tenant_id
    assert granted.capability_id == "social-operations"
    assert granted.granted_by == actor
    assert granted.granted_at == _NOW
    assert granted.expires_at is None
    assert granted.revision == 1

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        loaded = await repository.get(tenant_id=tenant_id, capability_id="social-operations")
    assert loaded is not None
    assert loaded.id == granted.id
    assert loaded.status is EntitlementStatus.ACTIVE


@pytest.mark.asyncio
async def test_regrant_is_idempotent_and_updates_terms(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    expiry = _NOW + timedelta(days=30)
    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        first = await repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW,
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        second = await repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="contract",
            expires_at=expiry,
            now=_NOW + timedelta(minutes=1),
        )
        await session.commit()

    assert second.id == first.id
    assert second.revision == first.revision + 1
    assert second.status is EntitlementStatus.ACTIVE
    assert second.expires_at == expiry
    assert second.source == "contract"

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        rows = await repository.list_for_tenant(tenant_id=tenant_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_revoke_marks_record_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    revoker = uuid4()
    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        await repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW,
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        revoked = await repository.revoke(
            tenant_id=tenant_id,
            capability_id="social-operations",
            revoked_by=revoker,
            now=_NOW + timedelta(minutes=5),
        )
        await session.commit()

    assert revoked is not None
    assert revoked.status is EntitlementStatus.REVOKED
    assert revoked.revoked_by == revoker
    assert revoked.revoked_at == _NOW + timedelta(minutes=5)
    assert not revoked.is_effective(now=_NOW + timedelta(minutes=6))

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        repeated = await repository.revoke(
            tenant_id=tenant_id,
            capability_id="social-operations",
            revoked_by=uuid4(),
            now=_NOW + timedelta(minutes=10),
        )
        await session.commit()

    assert repeated is not None
    assert repeated.status is EntitlementStatus.REVOKED
    assert repeated.revoked_by == revoker
    assert repeated.revoked_at == _NOW + timedelta(minutes=5)
    assert repeated.revision == revoked.revision


@pytest.mark.asyncio
async def test_revoke_missing_record_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        result = await repository.revoke(
            tenant_id=uuid4(),
            capability_id="social-operations",
            revoked_by=uuid4(),
            now=_NOW,
        )
    assert result is None


@pytest.mark.asyncio
async def test_grant_after_revoke_reactivates_same_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = uuid4()
    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        first = await repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW,
        )
        await repository.revoke(
            tenant_id=tenant_id,
            capability_id="social-operations",
            revoked_by=uuid4(),
            now=_NOW + timedelta(minutes=1),
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        regranted = await repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW + timedelta(minutes=2),
        )
        await session.commit()

    assert regranted.id == first.id
    assert regranted.status is EntitlementStatus.ACTIVE
    assert regranted.granted_at == _NOW + timedelta(minutes=2)
    assert regranted.revoked_at is None
    assert regranted.revoked_by is None

    async with session_factory() as session:
        rows = await SqlAlchemyCapabilityEntitlementRepository(session).list_for_tenant(
            tenant_id=tenant_id
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_tenant_isolation_between_entitlements(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        await repository.grant(
            tenant_id=tenant_a,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW,
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        assert await repository.get(tenant_id=tenant_b, capability_id="social-operations") is None
        assert await repository.list_for_tenant(tenant_id=tenant_b) == []

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        await repository.revoke(
            tenant_id=tenant_b,
            capability_id="social-operations",
            revoked_by=uuid4(),
            now=_NOW,
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyCapabilityEntitlementRepository(session)
        untouched = await repository.get(tenant_id=tenant_a, capability_id="social-operations")
    assert untouched is not None
    assert untouched.status is EntitlementStatus.ACTIVE


@pytest.mark.asyncio
async def test_concurrent_grants_converge_to_single_active_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two writers racing on the same tenant/capability must not duplicate rows."""

    tenant_id = uuid4()
    async with session_factory() as first_session, session_factory() as second_session:
        first_repository = SqlAlchemyCapabilityEntitlementRepository(first_session)
        second_repository = SqlAlchemyCapabilityEntitlementRepository(second_session)

        first = await first_repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW,
        )
        await first_session.commit()

        second = await second_repository.grant(
            tenant_id=tenant_id,
            capability_id="social-operations",
            granted_by=uuid4(),
            source="manual",
            expires_at=None,
            now=_NOW + timedelta(seconds=1),
        )
        await second_session.commit()

    assert second.id == first.id
    assert second.revision == first.revision + 1

    async with session_factory() as session:
        rows = await SqlAlchemyCapabilityEntitlementRepository(session).list_for_tenant(
            tenant_id=tenant_id
        )
    assert len(rows) == 1
