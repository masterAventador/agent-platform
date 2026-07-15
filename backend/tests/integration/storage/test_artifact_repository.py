import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.engine import create_database_engine
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyArtifactRepository,
    SqlAlchemyArtifactStorageOperationRepository,
    SqlAlchemyFileRepository,
    SqlAlchemyTaskAttachmentRepository,
)
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.employees import EmployeeRecord
from agent_platform.infrastructure.database.repositories.runs import (
    RunRecord,
    SqlAlchemyRunEventRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantRecord
from agent_platform.platform.artifacts.entities import (
    Artifact,
    File,
    StorageOperation,
    TaskAttachment,
)
from agent_platform.platform.runs.events import EventType, PlatformEvent


@pytest.mark.asyncio
async def test_artifact_repositories_keep_tenant_and_run_boundaries() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    run_id = uuid4()
    user_id = uuid4()
    file = File.create(
        tenant_id=tenant_id,
        owner_id=user_id,
        name="brief.txt",
        media_type="text/plain",
        content=b"brief",
    )
    attachment = TaskAttachment.create(
        tenant_id=tenant_id,
        run_id=run_id,
        file_id=file.id,
        workspace_path="inputs/brief.txt",
    )
    artifact = Artifact.create(
        tenant_id=tenant_id,
        run_id=run_id,
        created_by=user_id,
        name="answer.txt",
        media_type="text/plain",
        content=b"answer",
    )

    async with sessions() as session:
        files = SqlAlchemyFileRepository(session)
        attachments = SqlAlchemyTaskAttachmentRepository(session)
        artifacts = SqlAlchemyArtifactRepository(session)
        await files.add(file)
        await attachments.add(attachment)
        await artifacts.add(artifact)
        await session.commit()

        assert await files.get(tenant_id=tenant_id, file_id=file.id) == file
        assert await files.get(tenant_id=other_tenant_id, file_id=file.id) is None
        assert await attachments.list_for_run(tenant_id=tenant_id, run_id=run_id) == [attachment]
        assert await attachments.list_for_run(tenant_id=other_tenant_id, run_id=run_id) == []
        assert await artifacts.list_for_run(tenant_id=tenant_id, run_id=run_id) == [artifact]
        assert await artifacts.get(tenant_id=other_tenant_id, artifact_id=artifact.id) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_artifact_repositories_enforce_composite_tenant_boundaries() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL 产物仓储测试")

    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    user_id = uuid4()
    employee_id = uuid4()
    run_id = uuid4()
    file = File.create(
        tenant_id=tenant_id,
        owner_id=user_id,
        name="brief.txt",
        media_type="text/plain",
        content=b"brief",
    )
    attachment = TaskAttachment.create(
        tenant_id=tenant_id,
        run_id=run_id,
        file_id=file.id,
        workspace_path=f"inputs/{file.id}/brief.txt",
    )
    artifact = Artifact.create(
        tenant_id=tenant_id,
        run_id=run_id,
        created_by=user_id,
        name="answer.txt",
        media_type="text/plain",
        content=b"answer",
    )

    try:
        async with sessions() as session:
            session.add_all(
                [
                    TenantRecord(
                        id=tenant_id,
                        name="产物租户",
                        slug=f"artifacts-{tenant_id}",
                        created_at=now,
                    ),
                    TenantRecord(
                        id=other_tenant_id,
                        name="其他租户",
                        slug=f"artifacts-{other_tenant_id}",
                        created_at=now,
                    ),
                    UserRecord(
                        id=user_id,
                        email=f"artifacts-{user_id}@example.com",
                        password_hash="hash",
                        email_verified=True,
                        created_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add(
                EmployeeRecord(
                    id=employee_id,
                    tenant_id=tenant_id,
                    created_by=user_id,
                    name="产物测试员工",
                    avatar_url=None,
                    role_description="真实 PostgreSQL 约束测试",
                    visibility="tenant",
                    runtime_type="autonomous",
                    system_prompt="生成产物",
                    model_settings={},
                    input_schema={},
                    output_schema={},
                    capabilities={"file_upload": True},
                    skill_ids=[],
                    tool_ids=[],
                    knowledge_base_ids=[],
                    approval_policy={},
                    release_strategy={},
                    status="published",
                    published_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant_id,
                    employee_id=employee_id,
                    employee_version=1,
                    created_by=user_id,
                    thread_id=f"artifact-test-{run_id}",
                    input_data={},
                    status="queued",
                    created_at=now,
                    updated_at=now,
                    started_at=None,
                    finished_at=None,
                    error_code=None,
                    error_message=None,
                )
            )
            await session.flush()
            files = SqlAlchemyFileRepository(session)
            attachments = SqlAlchemyTaskAttachmentRepository(session)
            artifacts = SqlAlchemyArtifactRepository(session)
            await files.add(file)
            await attachments.add(attachment)
            await artifacts.add(artifact)
            await session.commit()

            assert await attachments.list_for_run(tenant_id=tenant_id, run_id=run_id) == [
                attachment
            ]
            assert await artifacts.list_for_run(tenant_id=tenant_id, run_id=run_id) == [artifact]

            async def append_event(index: int) -> int:
                async with sessions() as event_session:
                    events = SqlAlchemyRunEventRepository(event_session)
                    sequence = await events.next_sequence(run_id=run_id)
                    await events.append(
                        PlatformEvent.create(
                            tenant_id=tenant_id,
                            employee_id=employee_id,
                            run_id=run_id,
                            sequence=sequence,
                            event_type=EventType.RUN_PROGRESS,
                            payload={"index": index},
                        )
                    )
                    await event_session.commit()
                    return sequence

            sequences = await asyncio.gather(*(append_event(index) for index in range(8)))
            assert sorted(sequences) == list(range(1, 9))

            duplicate_path = TaskAttachment.create(
                tenant_id=tenant_id,
                run_id=run_id,
                file_id=file.id,
                workspace_path=attachment.workspace_path,
            )
            with pytest.raises(IntegrityError):
                await attachments.add(duplicate_path)
            await session.rollback()

            cross_tenant_attachment = TaskAttachment.create(
                tenant_id=other_tenant_id,
                run_id=run_id,
                file_id=file.id,
                workspace_path="inputs/cross-tenant.txt",
            )
            with pytest.raises(IntegrityError):
                await attachments.add(cross_tenant_attachment)
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_storage_operation_claim_is_exclusive_and_cas_protected() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("需要 TEST_DATABASE_URL 才运行真实 PostgreSQL Saga 领取测试")

    engine = create_database_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    foreground_owner = uuid4()
    first_reconciler = uuid4()
    second_reconciler = uuid4()
    expired_at = datetime.now(UTC) - timedelta(minutes=10)
    operation = StorageOperation.pending(
        tenant_id=tenant_id,
        action="put",
        entity_kind="artifact",
        entity_id=uuid4(),
        storage_key=f"saga/{uuid4()}",
        lease_owner=foreground_owner,
        now=expired_at,
        lease_duration=timedelta(minutes=1),
    )
    try:
        async with sessions() as setup_session:
            setup_session.add(
                TenantRecord(
                    id=tenant_id,
                    name="Saga 租约租户",
                    slug=f"artifact-saga-{tenant_id}",
                    created_at=datetime.now(UTC),
                )
            )
            await setup_session.flush()
            await SqlAlchemyArtifactStorageOperationRepository(setup_session).add(operation)
            await setup_session.commit()

        async with sessions() as first_session, sessions() as second_session:
            first_repository = SqlAlchemyArtifactStorageOperationRepository(first_session)
            second_repository = SqlAlchemyArtifactStorageOperationRepository(second_session)
            claimed_at = datetime.now(UTC)
            first_claim = await first_repository.claim_pending(
                lease_owner=first_reconciler,
                claimed_at=claimed_at,
                lease_expires_at=claimed_at + timedelta(minutes=5),
                limit=10,
            )
            second_claim = await second_repository.claim_pending(
                lease_owner=second_reconciler,
                claimed_at=claimed_at,
                lease_expires_at=claimed_at + timedelta(minutes=5),
                limit=10,
            )
            assert [item.id for item in first_claim] == [operation.id]
            assert second_claim == []
            await first_session.commit()
            await second_session.rollback()

        async with sessions() as cas_session:
            repository = SqlAlchemyArtifactStorageOperationRepository(cas_session)
            rescan_at = datetime.now(UTC) + timedelta(seconds=5)
            assert not await repository.mark_status(
                operation_id=operation.id,
                expected_phase="intent",
                lease_owner=foreground_owner,
                status="completed",
            )
            assert await repository.mark_status(
                operation_id=operation.id,
                expected_phase="intent",
                lease_owner=first_reconciler,
                status="compensated",
                reconcile_after=rescan_at,
            )
            await cas_session.commit()

        async with sessions() as rescan_session:
            repository = SqlAlchemyArtifactStorageOperationRepository(rescan_session)
            tombstone_owner = uuid4()
            rescanned = await repository.claim_pending(
                lease_owner=tombstone_owner,
                claimed_at=rescan_at + timedelta(seconds=1),
                lease_expires_at=rescan_at + timedelta(seconds=6),
                limit=10,
            )
            assert [item.id for item in rescanned] == [operation.id]
            renewed_until = rescan_at + timedelta(minutes=1)
            assert await repository.renew_lease(
                operation_id=operation.id,
                expected_phase="intent",
                lease_owner=tombstone_owner,
                reconcile_after=renewed_until,
            )
            assert not await repository.renew_lease(
                operation_id=operation.id,
                expected_phase="intent",
                lease_owner=uuid4(),
                reconcile_after=renewed_until,
            )
            await rescan_session.commit()

        async with sessions() as renewed_session:
            repository = SqlAlchemyArtifactStorageOperationRepository(renewed_session)
            assert (
                await repository.claim_pending(
                    lease_owner=uuid4(),
                    claimed_at=rescan_at + timedelta(seconds=10),
                    lease_expires_at=rescan_at + timedelta(minutes=2),
                    limit=10,
                )
                == []
            )
            await renewed_session.rollback()
    finally:
        await engine.dispose()
