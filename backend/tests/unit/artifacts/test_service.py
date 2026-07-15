import asyncio
from dataclasses import replace
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

    async def mark_status(self, *, operation_id: UUID, status: str) -> None:
        operation = self.operations[operation_id]
        self.operations[operation_id] = replace(operation, status=status)

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
