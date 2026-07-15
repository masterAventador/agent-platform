from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_platform.platform.artifacts.entities import (
    Artifact,
    File,
    StorageOperation,
    TaskAttachment,
)


class ArtifactStorageProvider(Protocol):
    async def put(self, *, key: str, content: bytes, media_type: str) -> None: ...

    async def get(self, *, key: str) -> bytes: ...

    async def delete(self, *, key: str) -> None: ...


class FileRepository(Protocol):
    async def add(self, file: File) -> None: ...

    async def get(self, *, tenant_id: UUID, file_id: UUID) -> File | None: ...

    async def delete(self, *, tenant_id: UUID, file_id: UUID) -> bool: ...

    async def delete_if_unbound(self, *, tenant_id: UUID, file_id: UUID) -> bool: ...

    async def list_unbound_before(self, *, older_than: datetime, limit: int) -> list[File]: ...


class TaskAttachmentRepository(Protocol):
    async def add(self, attachment: TaskAttachment) -> None: ...

    async def list_for_run(self, *, tenant_id: UUID, run_id: UUID) -> list[TaskAttachment]: ...


class ArtifactRepository(Protocol):
    async def add(self, artifact: Artifact) -> None: ...

    async def get(self, *, tenant_id: UUID, artifact_id: UUID) -> Artifact | None: ...

    async def list_for_run(self, *, tenant_id: UUID, run_id: UUID) -> list[Artifact]: ...

    async def delete(self, *, tenant_id: UUID, artifact_id: UUID) -> bool: ...


class StorageOperationRepository(Protocol):
    async def add(self, operation: StorageOperation) -> None: ...

    async def lock_owned(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        now: datetime,
    ) -> bool: ...

    async def advance_phase(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        phase: str,
        reconcile_after: datetime,
    ) -> bool: ...

    async def renew_lease(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        reconcile_after: datetime,
    ) -> bool: ...

    async def mark_status(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        status: str,
        reconcile_after: datetime | None = None,
        retire_after: datetime | None = None,
    ) -> bool: ...

    async def release_claim(
        self,
        *,
        operation_id: UUID,
        expected_phase: str,
        lease_owner: UUID,
        reconcile_after: datetime,
    ) -> bool: ...

    async def claim_pending(
        self,
        *,
        lease_owner: UUID,
        claimed_at: datetime,
        lease_expires_at: datetime,
        limit: int = 100,
    ) -> list[StorageOperation]: ...


class ArtifactWorkspace(Protocol):
    async def write_file(self, *, path: str, content: bytes) -> None: ...

    async def read_file(self, *, path: str) -> bytes: ...
