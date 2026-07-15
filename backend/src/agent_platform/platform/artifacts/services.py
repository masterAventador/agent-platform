import asyncio
import logging
from collections.abc import Awaitable, Callable
from hashlib import sha256
from uuid import UUID

from agent_platform.platform.artifacts.entities import Artifact, File
from agent_platform.platform.artifacts.ports import (
    ArtifactRepository,
    ArtifactStorageProvider,
    ArtifactWorkspace,
    FileRepository,
    TaskAttachmentRepository,
)

logger = logging.getLogger(__name__)


class ArtifactService:
    def __init__(
        self,
        *,
        file_repository: FileRepository,
        artifact_repository: ArtifactRepository | None = None,
        storage: ArtifactStorageProvider,
    ) -> None:
        self._files = file_repository
        self._artifacts = artifact_repository
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

    async def _cleanup_object(self, key: str) -> None:
        try:
            await self._storage.delete(key=key)
        except BaseException:
            logger.exception("artifact_upload_cleanup_failed", extra={"storage_key": key})

    def _required_artifact_repository(self) -> ArtifactRepository:
        if self._artifacts is None:
            raise RuntimeError("artifact repository is not configured")
        return self._artifacts

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
