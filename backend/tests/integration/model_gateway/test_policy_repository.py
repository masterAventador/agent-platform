from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.model_gateway import (
    ModelGatewayProvisioningCommandRecord,
    SqlAlchemyModelGatewayPolicyRepository,
    TenantModelGatewayPolicyRecord,
)
from agent_platform.platform.model_gateway.entities import TenantModelGatewayPolicy
from agent_platform.platform.model_gateway.errors import (
    CorruptModelGatewayPolicy,
    ModelGatewayPolicyPersistenceError,
    ModelGatewayPolicyRevisionConflict,
)
from agent_platform.platform.model_gateway.ports import ModelGatewayProvisioningAction


def _policy(tenant_id: UUID, updated_by: UUID, revision: int = 1) -> TenantModelGatewayPolicy:
    return TenantModelGatewayPolicy.create_desired(
        tenant_id=tenant_id,
        enabled=True,
        allowed_aliases={"general-purpose"},
        budget_microusd=1_000_000,
        budget_period="monthly",
        rpm_limit=60,
        tpm_limit=100_000,
        max_parallel_requests=4,
        revision=revision,
        updated_by=updated_by,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_repository_is_tenant_scoped_and_writes_outbox_atomically(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_a, tenant_b, user_id = uuid4(), uuid4(), uuid4()

    async with sessions() as session:
        repository = SqlAlchemyModelGatewayPolicyRepository(session)
        await repository.save_desired(
            _policy(tenant_a, user_id),
            expected_revision=0,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await repository.save_desired(
            _policy(tenant_b, user_id),
            expected_revision=0,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await session.commit()

    async with sessions() as session:
        repository = SqlAlchemyModelGatewayPolicyRepository(session)
        assert (await repository.get(tenant_a)).tenant_id == tenant_a  # type: ignore[union-attr]
        assert (await repository.get(uuid4())) is None
        commands = (
            await session.execute(select(ModelGatewayProvisioningCommandRecord))
        ).scalars().all()
        assert {(command.tenant_id, command.desired_revision) for command in commands} == {
            (tenant_a, 1),
            (tenant_b, 1),
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_fails_closed_for_corrupt_persisted_policy(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'corrupt.db'}")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, user_id = uuid4(), uuid4()

    async with sessions() as session:
        session.add(
            TenantModelGatewayPolicyRecord(
                tenant_id=tenant_id,
                enabled=True,
                allowed_aliases=["untrusted-provider-model"],
                budget_microusd=1_000_000,
                budget_period="monthly",
                rpm_limit=60,
                tpm_limit=100_000,
                max_parallel_requests=4,
                revision=1,
                status="active",
                created_at=datetime(2026, 7, 14, tzinfo=UTC),
                updated_at=datetime(2026, 7, 15, tzinfo=UTC),
                updated_by=user_id,
            )
        )
        await session.commit()

    async with sessions() as session:
        with pytest.raises(CorruptModelGatewayPolicy):
            await SqlAlchemyModelGatewayPolicyRepository(session).get(tenant_id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_stable_persistence_failure_not_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'integrity.db'}")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, user_id = uuid4(), uuid4()

    async with sessions() as session:
        repository = SqlAlchemyModelGatewayPolicyRepository(session)
        initial = _policy(tenant_id, user_id)
        await repository.save_desired(
            initial,
            expected_revision=0,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await session.commit()
        revised = initial.revise_desired(
            enabled=False,
            allowed_aliases={"general-purpose"},
            budget_microusd=1_000_000,
            budget_period="monthly",
            rpm_limit=60,
            tpm_limit=100_000,
            max_parallel_requests=4,
            updated_by=user_id,
            now=datetime(2026, 7, 15, tzinfo=UTC),
        )

        async def fail_flush(objects: object | None = None) -> None:
            del objects
            raise IntegrityError(
                "sensitive statement", {"secret": "must-not-leak"}, Exception("db detail")
            )

        monkeypatch.setattr(session, "flush", fail_flush)
        with pytest.raises(ModelGatewayPolicyPersistenceError) as captured:
            await repository.save_desired(
                revised,
                expected_revision=1,
                action=ModelGatewayProvisioningAction.RECONCILE,
            )
        assert not isinstance(captured.value, ModelGatewayPolicyRevisionConflict)
        assert "must-not-leak" not in str(captured.value)
    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_revision_is_rejected_and_does_not_duplicate_outbox(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'revision.db'}")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, user_id = uuid4(), uuid4()

    async with sessions() as session:
        repository = SqlAlchemyModelGatewayPolicyRepository(session)
        await repository.save_desired(
            _policy(tenant_id, user_id),
            expected_revision=0,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await session.commit()

    first_session, stale_session = sessions(), sessions()
    try:
        first = SqlAlchemyModelGatewayPolicyRepository(first_session)
        stale = SqlAlchemyModelGatewayPolicyRepository(stale_session)
        first_loaded = await first.get(tenant_id)
        stale_loaded = await stale.get(tenant_id)
        assert first_loaded is not None and stale_loaded is not None
        await first.save_desired(
            first_loaded.revise_desired(
                enabled=False,
                allowed_aliases={"general-purpose"},
                budget_microusd=1_000_000,
                budget_period="monthly",
                rpm_limit=60,
                tpm_limit=100_000,
                max_parallel_requests=4,
                updated_by=user_id,
                now=datetime(2026, 7, 15, tzinfo=UTC),
            ),
            expected_revision=1,
            action=ModelGatewayProvisioningAction.RECONCILE,
        )
        await first_session.commit()

        with pytest.raises(ModelGatewayPolicyRevisionConflict):
            await stale.save_desired(
                stale_loaded.revise_desired(
                    enabled=True,
                    allowed_aliases={"general-purpose"},
                    budget_microusd=2_000_000,
                    budget_period="monthly",
                    rpm_limit=60,
                    tpm_limit=100_000,
                    max_parallel_requests=4,
                    updated_by=user_id,
                    now=datetime(2026, 7, 16, tzinfo=UTC),
                ),
                expected_revision=1,
                action=ModelGatewayProvisioningAction.RECONCILE,
            )
        await stale_session.rollback()
    finally:
        await first_session.close()
        await stale_session.close()

    async with sessions() as session:
        policy_count = await session.scalar(
            select(func.count()).select_from(TenantModelGatewayPolicyRecord)
        )
        command_count = await session.scalar(
            select(func.count()).select_from(ModelGatewayProvisioningCommandRecord)
        )
        assert policy_count == 1
        assert command_count == 2
    await engine.dispose()
