import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from agent_platform.platform.artifacts.entities import (
    Artifact,
    File,
    StorageOperation,
    TaskAttachment,
)
from agent_platform.platform.artifacts.services import ArtifactService, TaskAttachmentService


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        del media_type
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


class FailableStorage(MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.fail_put = False
        self.fail_delete = False
        self.put_started = asyncio.Event()
        self.release_put = asyncio.Event()
        self.block_put = False

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        self.put_started.set()
        if self.block_put:
            await self.release_put.wait()
        if self.fail_put:
            raise RuntimeError("object put failed")
        await super().put(key=key, content=content, media_type=media_type)

    async def delete(self, *, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("object delete failed")
        await super().delete(key=key)


class MemoryOperationRepository:
    def __init__(self) -> None:
        self.operations: dict[UUID, StorageOperation] = {}

    async def add(self, operation: StorageOperation) -> None:
        self.operations[operation.id] = operation

    async def lock_owned(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        now: datetime,
    ) -> bool:
        operation = self.operations[operation_id]
        return (
            operation.status == "pending"
            and operation.phase == expected_phase
            and operation.lease_owner == lease_owner
            and operation.reconcile_after > now
        )

    async def advance_phase(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        phase: str,
        reconcile_after: datetime,
    ) -> bool:
        operation = self.operations[operation_id]
        if not (
            operation.status == "pending"
            and operation.phase == expected_phase
            and operation.lease_owner == lease_owner
        ):
            return False
        self.operations[operation_id] = replace(
            operation,
            phase=phase,
            reconcile_after=reconcile_after,
            updated_at=datetime.now(UTC),
        )
        return True

    async def mark_status(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        status: str,
    ) -> bool:
        operation = self.operations[operation_id]
        if not (
            operation.status == "pending"
            and operation.phase == expected_phase
            and operation.lease_owner == lease_owner
        ):
            return False
        self.operations[operation_id] = replace(
            operation,
            status=status,
            lease_owner=None,
            updated_at=datetime.now(UTC),
        )
        return True

    async def release_claim(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        reconcile_after: datetime,
    ) -> bool:
        operation = self.operations[operation_id]
        if not (
            operation.status == "pending"
            and operation.phase == expected_phase
            and operation.lease_owner == lease_owner
        ):
            return False
        self.operations[operation_id] = replace(
            operation,
            lease_owner=None,
            reconcile_after=reconcile_after,
            updated_at=datetime.now(UTC),
        )
        return True

    async def claim_pending(
        self,
        *,
        lease_owner: UUID,
        claimed_at: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> list[StorageOperation]:
        claimed: list[StorageOperation] = []
        for operation_id, operation in self.operations.items():
            if operation.status != "pending" or operation.reconcile_after > claimed_at:
                continue
            claimed_operation = replace(
                operation,
                lease_owner=lease_owner,
                reconcile_after=lease_expires_at,
                updated_at=claimed_at,
            )
            self.operations[operation_id] = claimed_operation
            claimed.append(claimed_operation)
            if len(claimed) == limit:
                break
        return claimed

    async def list_pending(self, *, limit: int) -> list[StorageOperation]:
        return [
            operation for operation in self.operations.values() if operation.status == "pending"
        ][:limit]


class FailingFileRepository:
    async def add(self, file: File) -> None:
        del file
        raise RuntimeError("database unavailable")

    async def get(self, *, tenant_id: UUID, file_id: UUID) -> File | None:
        del tenant_id, file_id
        return None

    async def delete(self, *, tenant_id: UUID, file_id: UUID) -> bool:
        del tenant_id, file_id
        return False


class CancelledFileRepository(FailingFileRepository):
    async def add(self, file: File) -> None:
        del file
        raise asyncio.CancelledError


class MemoryFileRepository:
    def __init__(self, file: File) -> None:
        self.file = file

    async def add(self, file: File) -> None:
        self.file = file

    async def get(self, *, tenant_id: UUID, file_id: UUID) -> File | None:
        if (tenant_id, file_id) == (self.file.tenant_id, self.file.id):
            return self.file
        return None

    async def delete(self, *, tenant_id: UUID, file_id: UUID) -> bool:
        del tenant_id, file_id
        return False


class MemoryAttachmentRepository:
    def __init__(self, attachment: TaskAttachment) -> None:
        self.attachment = attachment

    async def add(self, attachment: TaskAttachment) -> None:
        self.attachment = attachment

    async def list_for_run(self, *, tenant_id: UUID, run_id: UUID) -> list[TaskAttachment]:
        if (tenant_id, run_id) == (self.attachment.tenant_id, self.attachment.run_id):
            return [self.attachment]
        return []


class MemoryArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: dict[UUID, Artifact] = {}
        self.fail_delete = False

    async def add(self, artifact: Artifact) -> None:
        self.artifacts[artifact.id] = artifact

    async def get(self, *, tenant_id: UUID, artifact_id: UUID) -> Artifact | None:
        artifact = self.artifacts.get(artifact_id)
        return artifact if artifact is not None and artifact.tenant_id == tenant_id else None

    async def list_for_run(self, *, tenant_id: UUID, run_id: UUID) -> list[Artifact]:
        return [
            artifact
            for artifact in self.artifacts.values()
            if artifact.tenant_id == tenant_id and artifact.run_id == run_id
        ]

    async def delete(self, *, tenant_id: UUID, artifact_id: UUID) -> bool:
        if self.fail_delete:
            raise RuntimeError("database unavailable")
        artifact = await self.get(tenant_id=tenant_id, artifact_id=artifact_id)
        if artifact is None:
            return False
        del self.artifacts[artifact_id]
        return True


class TransactionalArtifactState:
    def __init__(self) -> None:
        self.artifacts: dict[UUID, Artifact] = {}
        self.operations: dict[UUID, StorageOperation] = {}


class ForegroundArtifactRepository(MemoryArtifactRepository):
    def __init__(self, state: TransactionalArtifactState) -> None:
        super().__init__()
        self._state = state

    async def commit(self) -> None:
        self._state.artifacts.update(self.artifacts)


class ReconcilerArtifactRepository(MemoryArtifactRepository):
    def __init__(self, state: TransactionalArtifactState) -> None:
        super().__init__()
        self.artifacts = state.artifacts


class ForegroundOperationRepository(MemoryOperationRepository):
    def __init__(self, state: TransactionalArtifactState) -> None:
        super().__init__()
        self._state = state

    async def commit(self) -> None:
        self._state.operations.update(self.operations)


class ReconcilerOperationRepository(MemoryOperationRepository):
    def __init__(self, state: TransactionalArtifactState) -> None:
        super().__init__()
        self.operations = state.operations


class MemoryWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def write_file(self, *, path: str, content: bytes) -> None:
        self.files[path] = content

    async def read_file(self, *, path: str) -> bytes:
        return self.files[path]


@pytest.mark.asyncio
async def test_upload_cleans_object_when_metadata_persistence_fails() -> None:
    storage = MemoryStorage()
    service = ArtifactService(file_repository=FailingFileRepository(), storage=storage)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.upload_file(
            tenant_id=uuid4(),
            owner_id=uuid4(),
            name="brief.txt",
            media_type="text/plain",
            content=b"brief",
        )

    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_cancellation_still_cleans_successful_storage_write() -> None:
    storage = MemoryStorage()
    service = ArtifactService(file_repository=CancelledFileRepository(), storage=storage)

    with pytest.raises(asyncio.CancelledError):
        await service.upload_file(
            tenant_id=uuid4(),
            owner_id=uuid4(),
            name="brief.txt",
            media_type="text/plain",
            content=b"brief",
        )

    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_cleans_object_when_transaction_commit_fails() -> None:
    initial = File.create(
        tenant_id=uuid4(),
        owner_id=uuid4(),
        name="initial.txt",
        media_type="text/plain",
        content=b"initial",
    )
    storage = MemoryStorage()
    service = ArtifactService(
        file_repository=MemoryFileRepository(initial),
        storage=storage,
    )

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.upload_file(
            tenant_id=uuid4(),
            owner_id=uuid4(),
            name="brief.txt",
            media_type="text/plain",
            content=b"brief",
            commit=fail_commit,
        )

    assert storage.objects == {}


@pytest.mark.asyncio
async def test_task_attachments_are_materialized_from_verified_tenant_metadata() -> None:
    tenant_id, run_id, owner_id = uuid4(), uuid4(), uuid4()
    content = b"brief"
    file = File.create(
        tenant_id=tenant_id,
        owner_id=owner_id,
        name="brief.txt",
        media_type="text/plain",
        content=content,
    )
    attachment = TaskAttachment.create(
        tenant_id=tenant_id,
        run_id=run_id,
        file_id=file.id,
        workspace_path=f"inputs/{file.id}/brief.txt",
    )
    storage = MemoryStorage()
    storage.objects[file.storage_key] = content
    workspace = MemoryWorkspace()

    await TaskAttachmentService(
        file_repository=MemoryFileRepository(file),
        attachment_repository=MemoryAttachmentRepository(attachment),
        storage=storage,
    ).materialize(tenant_id=tenant_id, run_id=run_id, workspace=workspace)

    assert workspace.files == {f"/workspace/{attachment.workspace_path}": content}


@pytest.mark.asyncio
async def test_artifact_create_and_delete_use_one_repository_truth_source() -> None:
    tenant_id, run_id, creator_id = uuid4(), uuid4(), uuid4()
    repository = MemoryArtifactRepository()
    storage = MemoryStorage()
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        storage=storage,
    )

    artifact = await service.create_artifact(
        tenant_id=tenant_id,
        run_id=run_id,
        created_by=creator_id,
        name="result.txt",
        media_type="text/plain",
        content=b"done",
    )

    assert await repository.list_for_run(tenant_id=tenant_id, run_id=run_id) == [artifact]
    assert await service.read_artifact(artifact) == b"done"
    await service.delete_artifact(artifact)
    assert repository.artifacts == {}
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_artifact_create_cleans_object_when_transactional_event_fails() -> None:
    repository = MemoryArtifactRepository()
    storage = MemoryStorage()
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        storage=storage,
    )

    async def fail_event(_: Artifact) -> None:
        raise RuntimeError("event persistence failed")

    with pytest.raises(RuntimeError, match="event persistence failed"):
        await service.create_artifact(
            tenant_id=uuid4(),
            run_id=uuid4(),
            created_by=uuid4(),
            name="result.txt",
            media_type="text/plain",
            content=b"done",
            before_commit=fail_event,
        )

    assert storage.objects == {}


@pytest.mark.asyncio
async def test_artifact_delete_restores_object_when_metadata_delete_fails() -> None:
    tenant_id, run_id, creator_id = uuid4(), uuid4(), uuid4()
    repository = MemoryArtifactRepository()
    storage = MemoryStorage()
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        storage=storage,
    )
    artifact = await service.create_artifact(
        tenant_id=tenant_id,
        run_id=run_id,
        created_by=creator_id,
        name="result.txt",
        media_type="text/plain",
        content=b"done",
    )
    repository.fail_delete = True

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.delete_artifact(artifact)

    assert storage.objects[artifact.storage_key] == b"done"


@pytest.mark.asyncio
async def test_artifact_delete_restores_object_when_transaction_commit_fails() -> None:
    repository = MemoryArtifactRepository()
    storage = MemoryStorage()
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        storage=storage,
    )
    artifact = await service.create_artifact(
        tenant_id=uuid4(),
        run_id=uuid4(),
        created_by=uuid4(),
        name="result.txt",
        media_type="text/plain",
        content=b"done",
    )

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.delete_artifact(artifact, commit=fail_commit)

    assert storage.objects[artifact.storage_key] == b"done"


@pytest.mark.asyncio
async def test_durable_create_reconciles_a_failed_object_put_without_dangling_metadata() -> None:
    repository = MemoryArtifactRepository()
    operations = MemoryOperationRepository()
    storage = FailableStorage()
    storage.fail_put = True
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        operation_repository=operations,
        storage=storage,
    )

    async def commit() -> None:
        return None

    with pytest.raises(RuntimeError, match="object put failed"):
        await service.create_artifact(
            tenant_id=uuid4(),
            run_id=uuid4(),
            created_by=uuid4(),
            name="result.txt",
            media_type="text/plain",
            content=b"done",
            commit=commit,
        )

    pending = await operations.list_pending(limit=10)
    assert len(pending) == 1
    assert repository.artifacts == {}

    storage.fail_put = False
    assert await service.reconcile_pending(commit=commit) == 1
    assert operations.operations[pending[0].id].status == "compensated"
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_reconciler_claims_only_expired_operations_and_rejects_stale_owner_cas() -> None:
    operations = MemoryOperationRepository()
    foreground_owner = uuid4()
    reconciler_owner = uuid4()
    now = datetime.now(UTC)
    operation = StorageOperation.pending(
        tenant_id=uuid4(),
        action="put",
        entity_kind="artifact",
        entity_id=uuid4(),
        storage_key="artifact/key",
        lease_owner=foreground_owner,
        now=now,
        lease_duration=timedelta(seconds=5),
    )
    await operations.add(operation)

    assert (
        await operations.claim_pending(
            lease_owner=reconciler_owner,
            claimed_at=now + timedelta(seconds=4),
            lease_expires_at=now + timedelta(seconds=20),
            limit=10,
        )
        == []
    )
    claimed = await operations.claim_pending(
        lease_owner=reconciler_owner,
        claimed_at=now + timedelta(seconds=6),
        lease_expires_at=now + timedelta(seconds=20),
        limit=10,
    )
    assert [item.id for item in claimed] == [operation.id]
    assert not await operations.mark_status(
        operation_id=operation.id,
        expected_phase="intent",
        lease_owner=foreground_owner,
        status="completed",
    )
    assert await operations.mark_status(
        operation_id=operation.id,
        expected_phase="intent",
        lease_owner=reconciler_owner,
        status="compensated",
    )


@pytest.mark.asyncio
async def test_durable_create_does_not_swallow_cancellation_after_object_store_applies() -> None:
    repository = MemoryArtifactRepository()
    operations = MemoryOperationRepository()
    storage = FailableStorage()
    storage.block_put = True
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        operation_repository=operations,
        storage=storage,
    )

    async def commit() -> None:
        return None

    task = asyncio.create_task(
        service.create_artifact(
            tenant_id=uuid4(),
            run_id=uuid4(),
            created_by=uuid4(),
            name="result.txt",
            media_type="text/plain",
            content=b"done",
            commit=commit,
        )
    )
    await storage.put_started.wait()
    task.cancel()
    storage.release_put.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    pending = await operations.list_pending(limit=10)
    assert len(pending) == 1
    assert storage.objects[pending[0].storage_key] == b"done"
    assert repository.artifacts == {}

    assert await service.reconcile_pending(commit=commit) == 1
    assert operations.operations[pending[0].id].status == "compensated"
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_durable_delete_retries_object_failure_from_persisted_intent() -> None:
    tenant_id, run_id, creator_id = uuid4(), uuid4(), uuid4()
    repository = MemoryArtifactRepository()
    operations = MemoryOperationRepository()
    storage = FailableStorage()
    service = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=repository,
        operation_repository=operations,
        storage=storage,
    )

    async def commit() -> None:
        return None

    artifact = await service.create_artifact(
        tenant_id=tenant_id,
        run_id=run_id,
        created_by=creator_id,
        name="result.txt",
        media_type="text/plain",
        content=b"done",
        commit=commit,
    )
    storage.fail_delete = True

    with pytest.raises(RuntimeError, match="object delete failed"):
        await service.delete_artifact(artifact, commit=commit)

    pending = await operations.list_pending(limit=10)
    assert len(pending) == 1
    assert pending[0].action == "delete"
    assert repository.artifacts == {}
    assert storage.objects[artifact.storage_key] == b"done"

    storage.fail_delete = False
    assert await service.reconcile_pending(commit=commit) == 1
    assert operations.operations[pending[0].id].status == "completed"
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_fresh_put_is_not_compensated_while_foreground_metadata_commit_is_active() -> None:
    state = TransactionalArtifactState()
    storage = MemoryStorage()
    foreground_artifacts = ForegroundArtifactRepository(state)
    foreground_operations = ForegroundOperationRepository(state)
    reconciler = ArtifactService(
        file_repository=FailingFileRepository(),
        artifact_repository=ReconcilerArtifactRepository(state),
        operation_repository=ReconcilerOperationRepository(state),
        storage=storage,
    )
    metadata_commit_started = asyncio.Event()
    release_metadata_commit = asyncio.Event()
    commit_count = 0

    async def foreground_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 1:
            await foreground_operations.commit()
            return
        metadata_commit_started.set()
        await release_metadata_commit.wait()
        await foreground_artifacts.commit()
        await foreground_operations.commit()

    async def reconciler_commit() -> None:
        return None

    create_task = asyncio.create_task(
        ArtifactService(
            file_repository=FailingFileRepository(),
            artifact_repository=foreground_artifacts,
            operation_repository=foreground_operations,
            storage=storage,
        ).create_artifact(
            tenant_id=uuid4(),
            run_id=uuid4(),
            created_by=uuid4(),
            name="result.txt",
            media_type="text/plain",
            content=b"done",
            commit=foreground_commit,
        )
    )
    await asyncio.wait_for(metadata_commit_started.wait(), timeout=1)

    try:
        reconciled = await reconciler.reconcile_pending(commit=reconciler_commit)
    finally:
        release_metadata_commit.set()
    artifact = await asyncio.wait_for(create_task, timeout=1)
    assert reconciled == 0
    operation = next(iter(state.operations.values()))
    assert operation.status == "completed"
    assert state.artifacts == {artifact.id: artifact}
    assert storage.objects == {artifact.storage_key: b"done"}
