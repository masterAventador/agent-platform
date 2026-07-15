import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
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

DEFAULT_STORAGE_OPERATION_LEASE = timedelta(minutes=5)
DEFAULT_STORAGE_OPERATION_HEARTBEAT_SECONDS = 30.0


class StorageOperationLeaseLost(RuntimeError):
    pass


class _StorageOperationHeartbeat:
    def __init__(
        self,
        *,
        repository: StorageOperationRepository,
        operation: StorageOperation,
        lease_duration: timedelta,
        interval_seconds: float,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._repository = repository
        self._operation = operation
        if operation.lease_owner is None:
            raise ValueError("storage operation heartbeat requires an owner")
        self._lease_owner = operation.lease_owner
        self._lease_duration = lease_duration
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._phase = operation.phase
        self._lock = asyncio.Lock()
        self._failure: BaseException | None = None
        self._task = asyncio.create_task(self._run())

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise StorageOperationLeaseLost(
                "artifact storage operation lease heartbeat failed"
            ) from self._failure

    async def stop(self) -> None:
        if not self._task.done():
            self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _run(self) -> None:
        try:
            while True:
                await self._sleep(self._interval_seconds)
                async with self._lock:
                    renewed = await self._repository.renew_lease(
                        operation_id=self._operation.id,
                        expected_phase=self._phase,
                        lease_owner=self._lease_owner,
                        reconcile_after=self._clock() + self._lease_duration,
                    )
                    if not renewed:
                        raise StorageOperationLeaseLost("artifact storage operation lease was lost")
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._failure = error


class ArtifactService:
    def __init__(
        self,
        *,
        file_repository: FileRepository,
        artifact_repository: ArtifactRepository | None = None,
        operation_repository: StorageOperationRepository | None = None,
        storage: ArtifactStorageProvider,
        operation_lease_duration: timedelta = DEFAULT_STORAGE_OPERATION_LEASE,
        operation_heartbeat_interval: float = DEFAULT_STORAGE_OPERATION_HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] | None = None,
        heartbeat_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if operation_lease_duration <= timedelta(0):
            raise ValueError("storage operation lease must be positive")
        if operation_heartbeat_interval <= 0:
            raise ValueError("storage operation heartbeat must be positive")
        if operation_heartbeat_interval >= operation_lease_duration.total_seconds():
            raise ValueError("storage operation heartbeat must be shorter than lease")
        self._files = file_repository
        self._artifacts = artifact_repository
        self._operations = operation_repository
        self._storage = storage
        self._operation_lease_duration = operation_lease_duration
        self._operation_heartbeat_interval = operation_heartbeat_interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._heartbeat_sleep = heartbeat_sleep

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
                now=self._clock(),
                lease_duration=self._operation_lease_duration,
            )
            await self._operations.add(operation)
            await commit()
            heartbeat = self._start_heartbeat(operation)
            try:
                await self._await_storage(
                    self._storage.put(
                        key=file.storage_key,
                        content=content,
                        media_type=file.media_type,
                    )
                )
                heartbeat.raise_if_failed()
                await self._advance_foreground_phase(
                    operation=operation,
                    expected_phase="intent",
                    phase="storage_applied",
                    lease_owner=lease_owner,
                    heartbeat=heartbeat,
                    commit=commit,
                )

                async def add_file_metadata() -> None:
                    await self._files.add(file)

                await self._complete_foreground_operation(
                    operation=operation,
                    expected_phase="storage_applied",
                    lease_owner=lease_owner,
                    heartbeat=heartbeat,
                    apply_metadata=add_file_metadata,
                    commit=commit,
                )
            except BaseException:
                await asyncio.shield(heartbeat.stop())
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase=heartbeat.phase,
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await heartbeat.stop()
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
                now=self._clock(),
                lease_duration=self._operation_lease_duration,
            )
            await self._operations.add(operation)
            await commit()
            heartbeat = self._start_heartbeat(operation)
            try:
                await self._await_storage(
                    self._storage.put(
                        key=artifact.storage_key,
                        content=content,
                        media_type=artifact.media_type,
                    )
                )
                heartbeat.raise_if_failed()
                await self._advance_foreground_phase(
                    operation=operation,
                    expected_phase="intent",
                    phase="storage_applied",
                    lease_owner=lease_owner,
                    heartbeat=heartbeat,
                    commit=commit,
                )

                async def add_artifact_metadata() -> None:
                    await repository.add(artifact)
                    if before_commit is not None:
                        await before_commit(artifact)

                await self._complete_foreground_operation(
                    operation=operation,
                    expected_phase="storage_applied",
                    lease_owner=lease_owner,
                    heartbeat=heartbeat,
                    apply_metadata=add_artifact_metadata,
                    commit=commit,
                )
            except BaseException:
                await asyncio.shield(heartbeat.stop())
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase=heartbeat.phase,
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await heartbeat.stop()
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
                now=self._clock(),
                lease_duration=self._operation_lease_duration,
            )
            await self._operations.add(operation)
            deleted = await repository.delete(
                tenant_id=artifact.tenant_id,
                artifact_id=artifact.id,
            )
            if not deleted:
                raise RuntimeError("artifact metadata disappeared during delete")
            await commit()
            heartbeat = self._start_heartbeat(operation)
            try:
                await self._await_storage(self._storage.delete(key=artifact.storage_key))
                heartbeat.raise_if_failed()
                await self._advance_foreground_phase(
                    operation=operation,
                    expected_phase="metadata_applied",
                    phase="storage_applied",
                    lease_owner=lease_owner,
                    heartbeat=heartbeat,
                    commit=commit,
                )

                async def metadata_already_deleted() -> None:
                    return None

                await self._complete_foreground_operation(
                    operation=operation,
                    expected_phase="storage_applied",
                    lease_owner=lease_owner,
                    heartbeat=heartbeat,
                    apply_metadata=metadata_already_deleted,
                    commit=commit,
                )
            except BaseException:
                await asyncio.shield(heartbeat.stop())
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase=heartbeat.phase,
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await heartbeat.stop()
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
        claimed_at = self._clock()
        operations = await self._operations.claim_pending(
            lease_owner=lease_owner,
            claimed_at=claimed_at,
            lease_expires_at=claimed_at + self._operation_lease_duration,
            limit=limit,
        )
        if not operations:
            return 0
        # 先提交领取结果，再访问对象存储；其他进程只能在租约到期后接管。
        await commit()
        reconciled = 0
        for operation in operations:
            heartbeat = self._start_heartbeat(operation)
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
                heartbeat.raise_if_failed()
                async with heartbeat.lock:
                    heartbeat.raise_if_failed()
                    updated = await self._operations.mark_status(
                        operation_id=operation.id,
                        expected_phase=operation.phase,
                        lease_owner=lease_owner,
                        status=status,
                        reconcile_after=(
                            self._clock() + self._operation_lease_duration
                            if status == "compensated"
                            else None
                        ),
                    )
                    if updated:
                        await commit()
            except BaseException:
                await asyncio.shield(heartbeat.stop())
                await asyncio.shield(
                    self._release_for_reconciliation(
                        operation=operation,
                        expected_phase=operation.phase,
                        lease_owner=lease_owner,
                        commit=commit,
                    )
                )
                raise
            await heartbeat.stop()
            if not updated:
                # 所有权已被别的进程接管时，禁止陈旧协调器覆盖新状态。
                continue
            reconciled += 1
        return reconciled

    async def _advance_foreground_phase(
        self,
        *,
        operation: StorageOperation,
        expected_phase: str,
        phase: str,
        lease_owner: UUID,
        heartbeat: _StorageOperationHeartbeat,
        commit: Callable[[], Awaitable[None]],
    ) -> None:
        operations = self._required_operation_repository()
        async with heartbeat.lock:
            heartbeat.raise_if_failed()
            updated = await operations.advance_phase(
                operation_id=operation.id,
                expected_phase=expected_phase,
                lease_owner=lease_owner,
                phase=phase,
                reconcile_after=self._clock() + self._operation_lease_duration,
            )
            if not updated:
                raise StorageOperationLeaseLost("artifact storage operation lease was lost")
            await commit()
            heartbeat.set_phase(phase)

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
            now=self._clock(),
        )
        if not locked:
            raise StorageOperationLeaseLost("artifact storage operation lease was lost")

    async def _complete_foreground_operation(
        self,
        *,
        operation: StorageOperation,
        expected_phase: str,
        lease_owner: UUID,
        heartbeat: _StorageOperationHeartbeat,
        apply_metadata: Callable[[], Awaitable[None]],
        commit: Callable[[], Awaitable[None]],
    ) -> None:
        async with heartbeat.lock:
            heartbeat.raise_if_failed()
            await self._lock_foreground_operation(
                operation=operation,
                expected_phase=expected_phase,
                lease_owner=lease_owner,
            )
            await apply_metadata()
            updated = await self._required_operation_repository().mark_status(
                operation_id=operation.id,
                expected_phase=expected_phase,
                lease_owner=lease_owner,
                status="completed",
            )
            if not updated:
                raise StorageOperationLeaseLost("artifact storage operation lease was lost")
            await commit()

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
            reconcile_after=self._clock(),
        )
        if released:
            await commit()

    def _start_heartbeat(self, operation: StorageOperation) -> _StorageOperationHeartbeat:
        return _StorageOperationHeartbeat(
            repository=self._required_operation_repository(),
            operation=operation,
            lease_duration=self._operation_lease_duration,
            interval_seconds=self._operation_heartbeat_interval,
            clock=self._clock,
            sleep=self._heartbeat_sleep,
        )

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
