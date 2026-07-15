import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from agent_platform.platform.artifacts.entities import Artifact, File, StorageOperation
from agent_platform.platform.artifacts.ports import (
    ArtifactRepository,
    ArtifactStorageProvider,
    ArtifactWorkspace,
    FileRepository,
    StorageOperationRepository,
    TaskAttachmentRepository,
)

logger = logging.getLogger(__name__)

STORAGE_OPERATION_LEASE = timedelta(minutes=5)


class StorageOperationLeaseLost(RuntimeError):
    pass


class ArtifactService:
    def __init__(
        self,
        *,
        file_repository: FileRepository,
        artifact_repository: ArtifactRepository | None = None,
        operation_repository: StorageOperationRepository | None = None,
        storage: ArtifactStorageProvider,
    ) -> None:
        self._files = file_repository
        self._artifacts = artifact_repository
        self._operations = operation_repository
        self._storage = storage

    async def upload_file(
        self,
        *,
        tenant_id: UUID,
        owner_id: UUID,
        name: str,
        media_type: str,
        content: bytes,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> File:
        file = File.create(
            tenant_id=tenant_id,
            owner_id=owner_id,
            name=name,
            media_type=media_type,
            content=content,
        )
        if self._operations is not None and commit is not None:
            lease_owner = uuid4()
            operation = StorageOperation.pending(
                tenant_id=file.tenant_id,
                action="put",
                entity_kind="file",
                entity_id=file.id,
                storage_key=file.storage_key,
                lease_owner=lease_owner,
            )
            await self._operations.add(operation)
            await commit()
            try:
                await self._await_storage(
                    self._storage.put(
                        key=file.storage_key,
                        content=content,
                        media_type=file.media_type,
                    )
                )
            except BaseException:
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase="intent",
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await self._advance_foreground_phase(
                operation=operation,
                expected_phase="intent",
                phase="storage_applied",
                lease_owner=lease_owner,
                commit=commit,
            )
            await self._lock_foreground_operation(
                operation=operation,
                expected_phase="storage_applied",
                lease_owner=lease_owner,
            )
            await self._files.add(file)
            await self._complete_foreground_operation(
                operation=operation,
                expected_phase="storage_applied",
                lease_owner=lease_owner,
            )
            await commit()
            return file
        await self._storage.put(
            key=file.storage_key,
            content=content,
            media_type=file.media_type,
        )
        try:
            await self._files.add(file)
            if commit is not None:
                await commit()
        except asyncio.CancelledError:
            await asyncio.shield(self._cleanup_object(file.storage_key))
            raise
        except Exception:
            await self._cleanup_object(file.storage_key)
            raise
        return file

    async def read_file(self, file: File) -> bytes:
        content = await self._storage.get(key=file.storage_key)
        self._assert_integrity(
            content=content, size_bytes=file.size_bytes, expected_sha256=file.sha256
        )
        return content

    async def create_artifact(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        created_by: UUID,
        name: str,
        media_type: str,
        content: bytes,
        before_commit: Callable[[Artifact], Awaitable[None]] | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> Artifact:
        repository = self._required_artifact_repository()
        artifact = Artifact.create(
            tenant_id=tenant_id,
            run_id=run_id,
            created_by=created_by,
            name=name,
            media_type=media_type,
            content=content,
        )
        if self._operations is not None and commit is not None:
            lease_owner = uuid4()
            operation = StorageOperation.pending(
                tenant_id=artifact.tenant_id,
                action="put",
                entity_kind="artifact",
                entity_id=artifact.id,
                storage_key=artifact.storage_key,
                lease_owner=lease_owner,
            )
            await self._operations.add(operation)
            await commit()
            try:
                await self._await_storage(
                    self._storage.put(
                        key=artifact.storage_key,
                        content=content,
                        media_type=artifact.media_type,
                    )
                )
            except BaseException:
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase="intent",
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await self._advance_foreground_phase(
                operation=operation,
                expected_phase="intent",
                phase="storage_applied",
                lease_owner=lease_owner,
                commit=commit,
            )
            await self._lock_foreground_operation(
                operation=operation,
                expected_phase="storage_applied",
                lease_owner=lease_owner,
            )
            await repository.add(artifact)
            if before_commit is not None:
                await before_commit(artifact)
            await self._complete_foreground_operation(
                operation=operation,
                expected_phase="storage_applied",
                lease_owner=lease_owner,
            )
            await commit()
            return artifact
        await self._storage.put(
            key=artifact.storage_key,
            content=content,
            media_type=artifact.media_type,
        )
        try:
            await repository.add(artifact)
            if before_commit is not None:
                await before_commit(artifact)
            if commit is not None:
                await commit()
        except asyncio.CancelledError:
            await asyncio.shield(self._cleanup_object(artifact.storage_key))
            raise
        except Exception:
            await self._cleanup_object(artifact.storage_key)
            raise
        return artifact

    async def read_artifact(self, artifact: Artifact) -> bytes:
        content = await self._storage.get(key=artifact.storage_key)
        self._assert_integrity(
            content=content,
            size_bytes=artifact.size_bytes,
            expected_sha256=artifact.sha256,
        )
        return content

    async def delete_artifact(
        self,
        artifact: Artifact,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        repository = self._required_artifact_repository()
        if self._operations is not None and commit is not None:
            lease_owner = uuid4()
            operation = StorageOperation.pending(
                tenant_id=artifact.tenant_id,
                action="delete",
                entity_kind="artifact",
                entity_id=artifact.id,
                storage_key=artifact.storage_key,
                lease_owner=lease_owner,
                phase="metadata_applied",
            )
            await self._operations.add(operation)
            deleted = await repository.delete(
                tenant_id=artifact.tenant_id,
                artifact_id=artifact.id,
            )
            if not deleted:
                raise RuntimeError("artifact metadata disappeared during delete")
            await commit()
            try:
                await self._await_storage(self._storage.delete(key=artifact.storage_key))
            except BaseException:
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase="metadata_applied",
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await self._advance_foreground_phase(
                operation=operation,
                expected_phase="metadata_applied",
                phase="storage_applied",
                lease_owner=lease_owner,
                commit=commit,
            )
            await self._lock_foreground_operation(
                operation=operation,
                expected_phase="storage_applied",
                lease_owner=lease_owner,
            )
            await self._complete_foreground_operation(
                operation=operation,
                expected_phase="storage_applied",
                lease_owner=lease_owner,
            )
            await commit()
            return
        content = await self.read_artifact(artifact)
        await self._storage.delete(key=artifact.storage_key)
        try:
            deleted = await repository.delete(
                tenant_id=artifact.tenant_id,
                artifact_id=artifact.id,
            )
            if not deleted:
                raise RuntimeError("artifact metadata disappeared during delete")
            if commit is not None:
                await commit()
        except BaseException:
            try:
                await asyncio.shield(
                    self._storage.put(
                        key=artifact.storage_key,
                        content=content,
                        media_type=artifact.media_type,
                    )
                )
            except BaseException:
                logger.exception(
                    "artifact_delete_compensation_failed",
                    extra={"artifact_id": str(artifact.id)},
                )
            raise

    async def reconcile_pending(
        self,
        *,
        commit: Callable[[], Awaitable[None]],
        limit: int = 100,
    ) -> int:
        if self._operations is None:
            return 0
        lease_owner = uuid4()
        claimed_at = datetime.now(UTC)
        operations = await self._operations.claim_pending(
            lease_owner=lease_owner,
            claimed_at=claimed_at,
            lease_expires_at=claimed_at + STORAGE_OPERATION_LEASE,
            limit=limit,
        )
        if not operations:
            return 0
        # 先提交领取结果，再访问对象存储；其他进程只能在租约到期后接管。
        await commit()
        reconciled = 0
        for operation in operations:
            try:
                if operation.action == "put":
                    exists = await self._entity_exists(operation)
                    if exists:
                        status = "completed"
                    else:
                        await self._await_storage(
                            self._storage.delete(key=operation.storage_key)
                        )
                        status = "compensated"
                else:
                    await self._await_storage(
                        self._storage.delete(key=operation.storage_key)
                    )
                    status = "completed"
            except BaseException:
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase=operation.phase,
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            updated = await self._operations.mark_status(
                operation_id=operation.id,
                expected_phase=operation.phase,
                lease_owner=lease_owner,
                status=status,
            )
            if not updated:
                # 所有权已被别的进程接管时，禁止陈旧协调器覆盖新状态。
                continue
            await commit()
            reconciled += 1
        return reconciled

    async def _advance_foreground_phase(
        self,
        *,
        operation: StorageOperation,
        expected_phase: str,
        phase: str,
        lease_owner: UUID,
        commit: Callable[[], Awaitable[None]],
    ) -> None:
        operations = self._required_operation_repository()
        updated = await operations.advance_phase(
            operation_id=operation.id,
            expected_phase=expected_phase,
            lease_owner=lease_owner,
            phase=phase,
            reconcile_after=datetime.now(UTC) + STORAGE_OPERATION_LEASE,
        )
        if not updated:
            raise StorageOperationLeaseLost("artifact storage operation lease was lost")
        await commit()

    async def _lock_foreground_operation(
        self,
        *,
        operation: StorageOperation,
        expected_phase: str,
        lease_owner: UUID,
    ) -> None:
        locked = await self._required_operation_repository().lock_owned(
            operation_id=operation.id,
            expected_phase=expected_phase,
            lease_owner=lease_owner,
            now=datetime.now(UTC),
        )
        if not locked:
            raise StorageOperationLeaseLost("artifact storage operation lease was lost")

    async def _complete_foreground_operation(
        self,
        *,
        operation: StorageOperation,
        expected_phase: str,
        lease_owner: UUID,
    ) -> None:
        updated = await self._required_operation_repository().mark_status(
            operation_id=operation.id,
            expected_phase=expected_phase,
            lease_owner=lease_owner,
            status="completed",
        )
        if not updated:
            raise StorageOperationLeaseLost("artifact storage operation lease was lost")

    async def _release_for_reconciliation(
        self,
        *,
        operation: StorageOperation,
        expected_phase: str,
        lease_owner: UUID,
        commit: Callable[[], Awaitable[None]],
    ) -> None:
        released = await self._required_operation_repository().release_claim(
            operation_id=operation.id,
            expected_phase=expected_phase,
            lease_owner=lease_owner,
            reconcile_after=datetime.now(UTC),
        )
        if released:
            await commit()

    async def _entity_exists(self, operation: StorageOperation) -> bool:
        if operation.entity_kind == "file":
            return (
                await self._files.get(
                    tenant_id=operation.tenant_id,
                    file_id=operation.entity_id,
                )
                is not None
            )
        repository = self._required_artifact_repository()
        return (
            await repository.get(
                tenant_id=operation.tenant_id,
                artifact_id=operation.entity_id,
            )
            is not None
        )

    @staticmethod
    async def _await_storage(operation: Awaitable[None]) -> None:
        task: asyncio.Future[None] = asyncio.ensure_future(operation)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)
            except Exception:
                logger.exception("artifact_storage_operation_failed_after_cancellation")
            raise

    async def _cleanup_object(self, key: str) -> None:
        try:
            await self._storage.delete(key=key)
        except BaseException:
            logger.exception("artifact_upload_cleanup_failed", extra={"storage_key": key})

    def _required_artifact_repository(self) -> ArtifactRepository:
        if self._artifacts is None:
            raise RuntimeError("artifact repository is not configured")
        return self._artifacts

    def _required_operation_repository(self) -> StorageOperationRepository:
        if self._operations is None:
            raise RuntimeError("artifact storage operation repository is not configured")
        return self._operations

    @staticmethod
    def _assert_integrity(
        *, content: bytes, size_bytes: int, expected_sha256: str
    ) -> None:
        if len(content) != size_bytes or sha256(content).hexdigest() != expected_sha256:
            raise RuntimeError("artifact content integrity mismatch")


class TaskAttachmentService:
    def __init__(
        self,
        *,
        file_repository: FileRepository,
        attachment_repository: TaskAttachmentRepository,
        storage: ArtifactStorageProvider,
    ) -> None:
        self._files = file_repository
        self._attachments = attachment_repository
        self._storage = storage

    async def materialize(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        workspace: ArtifactWorkspace,
    ) -> None:
        attachments = await self._attachments.list_for_run(
            tenant_id=tenant_id,
            run_id=run_id,
        )
        file_service = ArtifactService(file_repository=self._files, storage=self._storage)
        for attachment in attachments:
            file = await self._files.get(tenant_id=tenant_id, file_id=attachment.file_id)
            if file is None:
                raise RuntimeError("authorized task attachment metadata is missing")
            content = await file_service.read_file(file)
            await workspace.write_file(
                path=f"/workspace/{attachment.workspace_path}",
                content=content,
            )
