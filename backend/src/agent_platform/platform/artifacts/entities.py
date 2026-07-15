import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

MEDIA_TYPE_EXTENSIONS: dict[str, frozenset[str]] = {
    "application/json": frozenset({".json"}),
    "application/pdf": frozenset({".pdf"}),
    "application/vnd.ms-excel": frozenset({".xls"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": frozenset(
        {".xlsx"}
    ),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": frozenset(
        {".docx"}
    ),
    "application/zip": frozenset({".zip"}),
    "image/jpeg": frozenset({".jpeg", ".jpg"}),
    "image/png": frozenset({".png"}),
    "text/csv": frozenset({".csv"}),
    "text/markdown": frozenset({".markdown", ".md"}),
    "text/plain": frozenset({".log", ".txt"}),
}


class InvalidArtifactInput(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StorageOperation:
    id: UUID
    tenant_id: UUID
    action: str
    entity_kind: str
    entity_id: UUID
    storage_key: str
    status: str
    phase: str
    lease_owner: UUID | None
    reconcile_after: datetime
    created_at: datetime
    updated_at: datetime

    @classmethod
    def pending(
        cls,
        *,
        tenant_id: UUID,
        action: str,
        entity_kind: str,
        entity_id: UUID,
        storage_key: str,
        lease_owner: UUID,
        phase: str = "intent",
        now: datetime | None = None,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> "StorageOperation":
        if action not in {"put", "delete"} or entity_kind not in {"file", "artifact"}:
            raise ValueError("invalid storage operation")
        if phase not in {"intent", "metadata_applied", "storage_applied"}:
            raise ValueError("invalid storage operation phase")
        if lease_duration <= timedelta(0):
            raise ValueError("storage operation lease must be positive")
        current_time = now or datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            action=action,
            entity_kind=entity_kind,
            entity_id=entity_id,
            storage_key=storage_key,
            status="pending",
            phase=phase,
            lease_owner=lease_owner,
            reconcile_after=current_time + lease_duration,
            created_at=current_time,
            updated_at=current_time,
        )


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


def _validate_content(*, name: str, media_type: str, content: bytes) -> None:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise InvalidArtifactInput("文件类型不允许")
    if not content or len(content) > MAX_FILE_SIZE_BYTES:
        raise InvalidArtifactInput("文件大小无效")
    extension = PurePosixPath(name).suffix.lower()
    if extension not in MEDIA_TYPE_EXTENSIONS[media_type]:
        raise InvalidArtifactInput("文件扩展名与类型不一致")

    if media_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise InvalidArtifactInput("文件内容与类型不一致")
    if media_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise InvalidArtifactInput("文件内容与类型不一致")
    if media_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
        raise InvalidArtifactInput("文件内容与类型不一致")
    if media_type in {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    } and not content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        raise InvalidArtifactInput("文件内容与类型不一致")
    if media_type == "application/vnd.ms-excel" and not content.startswith(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    ):
        raise InvalidArtifactInput("文件内容与类型不一致")
    if media_type.startswith("text/") or media_type == "application/json":
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidArtifactInput("文件内容与类型不一致") from None
        if "\x00" in decoded:
            raise InvalidArtifactInput("文件内容与类型不一致")
        if media_type == "application/json":
            try:
                json.loads(decoded)
            except json.JSONDecodeError:
                raise InvalidArtifactInput("文件内容与类型不一致") from None


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
        _validate_content(name=safe_name, media_type=media_type, content=content)
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
        _validate_content(name=safe_name, media_type=media_type, content=content)
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
