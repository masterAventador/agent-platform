from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID, uuid4

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "image/jpeg",
        "image/png",
        "text/csv",
        "text/markdown",
        "text/plain",
    }
)


class InvalidArtifactInput(ValueError):
    pass


def _validate_name(name: str) -> str:
    normalized = name.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 for character in normalized)
        or len(normalized) > 255
    ):
        raise InvalidArtifactInput("文件名无效")
    return normalized


def _validate_content(*, media_type: str, content: bytes) -> None:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise InvalidArtifactInput("文件类型不允许")
    if not content or len(content) > MAX_FILE_SIZE_BYTES:
        raise InvalidArtifactInput("文件大小无效")


def validate_workspace_path(path: str) -> str:
    if "\\" in path:
        raise InvalidArtifactInput("工作区路径无效")
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        raise InvalidArtifactInput("工作区路径无效")
    return parsed.as_posix()


@dataclass(frozen=True, slots=True)
class File:
    id: UUID
    tenant_id: UUID
    owner_id: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        owner_id: UUID,
        name: str,
        media_type: str,
        content: bytes,
    ) -> "File":
        safe_name = _validate_name(name)
        _validate_content(media_type=media_type, content=content)
        file_id = uuid4()
        return cls(
            id=file_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            name=safe_name,
            media_type=media_type,
            size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
            storage_key=f"tenants/{tenant_id}/files/{file_id}",
            created_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class TaskAttachment:
    id: UUID
    tenant_id: UUID
    run_id: UUID
    file_id: UUID
    workspace_path: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        run_id: UUID,
        file_id: UUID,
        workspace_path: str,
    ) -> "TaskAttachment":
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            file_id=file_id,
            workspace_path=validate_workspace_path(workspace_path),
            created_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    id: UUID
    tenant_id: UUID
    run_id: UUID
    created_by: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        run_id: UUID,
        created_by: UUID,
        name: str,
        media_type: str,
        content: bytes,
    ) -> "Artifact":
        safe_name = _validate_name(name)
        _validate_content(media_type=media_type, content=content)
        artifact_id = uuid4()
        return cls(
            id=artifact_id,
            tenant_id=tenant_id,
            run_id=run_id,
            created_by=created_by,
            name=safe_name,
            media_type=media_type,
            size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
            storage_key=(
                f"tenants/{tenant_id}/runs/{run_id}/artifacts/{artifact_id}"
            ),
            created_at=datetime.now(UTC),
        )
