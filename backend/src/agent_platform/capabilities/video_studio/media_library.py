from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedMaterialPreview,
    IssuedUploadCredentials,
    MaterialObjectCleaner,
    MaterialObjectMissing,
    MaterialObjectVerifier,
    MaterialPreviewUrlIssuer,
    MaterialUploadCredentialIssuer,
)

MAX_MATERIAL_SIZE_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_UPLOAD_CREDENTIAL_TTL = timedelta(minutes=15)
DEFAULT_PREVIEW_URL_TTL = timedelta(minutes=5)
MATERIAL_UPLOAD_ACTIONS = (
    "name/cos:PutObject",
    "name/cos:PostObject",
    "name/cos:InitiateMultipartUpload",
    "name/cos:UploadPart",
    "name/cos:CompleteMultipartUpload",
    "name/cos:AbortMultipartUpload",
)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_CRC64_RE = re.compile(r"^[0-9]{1,20}$")
_MAX_UINT64 = 2**64 - 1
_DOWNLOAD_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
MAX_RESUME_TOKEN_LENGTH = 500
_MEDIA_EXTENSIONS: dict[str, frozenset[str]] = {
    "video/mp4": frozenset({".mp4"}),
    "video/quicktime": frozenset({".mov"}),
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "audio/mpeg": frozenset({".mp3"}),
    "audio/wav": frozenset({".wav"}),
}


class MaterialLibraryError(ValueError):
    pass


class InvalidMaterialInput(MaterialLibraryError):
    pass


class MaterialNotFoundError(MaterialLibraryError):
    pass


class MaterialFolderNotFoundError(MaterialLibraryError):
    pass


class MaterialInUseError(MaterialLibraryError):
    pass


class MaterialReferenceAlreadyExistsError(MaterialLibraryError):
    pass


class UploadCredentialExpiredError(MaterialLibraryError):
    pass


class InvalidDownloadTaskTransition(MaterialLibraryError):
    pass


class DownloadTaskConcurrentUpdateError(InvalidDownloadTaskTransition):
    pass


class MaterialKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    MUSIC = "music"


class DownloadTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class MaterialFolder:
    id: UUID
    tenant_id: UUID
    parent_id: UUID | None
    name: str
    created_by: UUID
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        name: str,
        parent_id: UUID | None,
        now: datetime,
    ) -> MaterialFolder:
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            parent_id=parent_id,
            name=_validate_folder_name(name),
            created_by=actor_id,
            created_at=now,
        )


@dataclass(frozen=True, slots=True)
class Material:
    id: UUID
    tenant_id: UUID
    owner_id: UUID
    folder_id: UUID | None
    name: str
    kind: MaterialKind
    media_type: str
    size_bytes: int
    sha256: str
    crc64ecma: str
    storage_key: str
    status: str
    tags: tuple[str, ...]
    upload_expires_at: datetime
    cleanup_required: bool
    artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @classmethod
    def pending_upload(
        cls,
        *,
        tenant_id: UUID,
        owner_id: UUID,
        name: str,
        kind: MaterialKind,
        media_type: str,
        size_bytes: int,
        sha256: str,
        crc64ecma: str,
        tag_names: tuple[str, ...],
        folder_id: UUID | None,
        upload_expires_at: datetime,
        now: datetime,
        artifact_id: UUID | None = None,
    ) -> Material:
        safe_name = _validate_material_input(
            name=name,
            kind=kind,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            crc64ecma=crc64ecma,
        )
        material_id = uuid4()
        return cls(
            id=material_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            folder_id=folder_id,
            name=safe_name,
            kind=kind,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            crc64ecma=crc64ecma,
            storage_key=f"materials/{tenant_id}/{material_id}/{safe_name}",
            status="pending_upload",
            tags=_normalize_tags(tag_names),
            upload_expires_at=upload_expires_at,
            cleanup_required=False,
            artifact_id=artifact_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )


@dataclass(frozen=True, slots=True)
class MaterialReference:
    id: UUID
    tenant_id: UUID
    material_id: UUID
    reference_type: str
    reference_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadTask:
    id: UUID
    tenant_id: UUID
    requested_by: UUID
    source_type: str
    source_id: UUID
    status: DownloadTaskStatus
    progress: int
    downloaded_bytes: int
    total_bytes: int
    resume_token: str | None
    error_code: str | None
    retryable: bool
    retry_count: int
    revision: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MaterialUploadDraft:
    material: Material
    credentials: IssuedUploadCredentials


@dataclass(frozen=True, slots=True)
class MediaLibraryAuditEvent:
    """素材库关键操作的脱敏审计事件（桥接 C14 统一审计协议）。

    details 只允许携带业务标识与摘要字段，禁止放入临时凭据、
    session token 或对象摘要（sha256）等敏感内容。
    """

    event_id: UUID
    action: str
    tenant_id: UUID
    actor_user_id: UUID
    resource_id: UUID
    occurred_at: datetime
    details: tuple[tuple[str, str], ...]


class MaterialAuditSink(Protocol):
    def record(self, event: MediaLibraryAuditEvent) -> None: ...


DOWNLOAD_SOURCE_MATERIAL = "material"
DOWNLOAD_SOURCE_ARTIFACT = "artifact"


@dataclass(frozen=True, slots=True)
class ResolvedDownloadSource:
    """成片（Core Artifact）来源解析结果：只暴露下载所需的可信元数据。"""

    size_bytes: int


class ArtifactDownloadSourceResolver(Protocol):
    async def resolve_artifact(
        self,
        *,
        tenant_id: UUID,
        artifact_id: UUID,
    ) -> ResolvedDownloadSource | None: ...


class MaterialRepository(Protocol):
    async def add_folder(self, folder: MaterialFolder) -> None: ...

    async def get_folder(self, *, tenant_id: UUID, folder_id: UUID) -> MaterialFolder | None: ...

    async def list_folders(
        self,
        *,
        tenant_id: UUID,
        parent_id: UUID | None = None,
    ) -> list[MaterialFolder]: ...

    async def add_material(self, material: Material) -> None: ...

    async def update_material(self, material: Material) -> None: ...

    async def get_material(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
        for_update: bool = False,
    ) -> Material | None: ...

    async def list_materials(self, *, tenant_id: UUID) -> list[Material]: ...

    async def list_expired_upload_drafts(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[Material]: ...

    async def find_active_upload_draft(
        self,
        *,
        tenant_id: UUID,
        sha256: str,
        size_bytes: int,
        folder_id: UUID | None,
        now: datetime,
    ) -> Material | None: ...

    async def list_cleanup_required_materials(self, *, limit: int) -> list[Material]: ...

    async def add_reference(self, reference: MaterialReference) -> None: ...

    async def list_references(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
    ) -> list[MaterialReference]: ...

    async def count_references(self, *, tenant_id: UUID, material_id: UUID) -> int: ...

    async def add_download_task(self, task: DownloadTask) -> None: ...

    async def update_download_task(
        self,
        task: DownloadTask,
        *,
        expected_revision: int,
    ) -> bool: ...

    async def get_download_task(self, *, tenant_id: UUID, task_id: UUID) -> DownloadTask | None: ...

    async def list_download_tasks(self, *, tenant_id: UUID) -> list[DownloadTask]: ...


class InMemoryMaterialRepository:
    def __init__(self) -> None:
        self.folders: dict[tuple[UUID, UUID], MaterialFolder] = {}
        self.materials: dict[tuple[UUID, UUID], Material] = {}
        self.references: list[MaterialReference] = []
        self.download_tasks: dict[tuple[UUID, UUID], DownloadTask] = {}

    async def add_folder(self, folder: MaterialFolder) -> None:
        self.folders[(folder.tenant_id, folder.id)] = folder

    async def get_folder(self, *, tenant_id: UUID, folder_id: UUID) -> MaterialFolder | None:
        return self.folders.get((tenant_id, folder_id))

    async def list_folders(
        self,
        *,
        tenant_id: UUID,
        parent_id: UUID | None = None,
    ) -> list[MaterialFolder]:
        return [
            folder
            for (stored_tenant_id, _), folder in self.folders.items()
            if stored_tenant_id == tenant_id
            and (parent_id is None or folder.parent_id == parent_id)
        ]

    async def add_material(self, material: Material) -> None:
        self.materials[(material.tenant_id, material.id)] = material

    async def update_material(self, material: Material) -> None:
        self.materials[(material.tenant_id, material.id)] = material

    async def get_material(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
        for_update: bool = False,
    ) -> Material | None:
        del for_update  # 内存仓储无并发事务语义；行级锁由 SQL 仓储在真实 PG 上提供。
        return self.materials.get((tenant_id, material_id))

    async def list_materials(self, *, tenant_id: UUID) -> list[Material]:
        return [
            material
            for (stored_tenant_id, _), material in self.materials.items()
            if stored_tenant_id == tenant_id and material.deleted_at is None
        ]

    async def list_expired_upload_drafts(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[Material]:
        return sorted(
            (
                material
                for material in self.materials.values()
                if material.status == "pending_upload"
                and _as_utc(material.upload_expires_at) < _as_utc(now)
            ),
            key=lambda material: (material.upload_expires_at, material.id),
        )[:limit]

    async def find_active_upload_draft(
        self,
        *,
        tenant_id: UUID,
        sha256: str,
        size_bytes: int,
        folder_id: UUID | None,
        now: datetime,
    ) -> Material | None:
        candidates = sorted(
            (
                material
                for material in self.materials.values()
                if material.tenant_id == tenant_id
                and material.status == "pending_upload"
                and material.deleted_at is None
                and material.sha256 == sha256
                and material.size_bytes == size_bytes
                and material.folder_id == folder_id
                and _as_utc(material.upload_expires_at) > _as_utc(now)
            ),
            key=lambda material: (material.created_at, material.id),
        )
        return candidates[0] if candidates else None

    async def list_cleanup_required_materials(self, *, limit: int) -> list[Material]:
        return sorted(
            (
                material
                for material in self.materials.values()
                if material.cleanup_required
            ),
            key=lambda material: (material.updated_at, material.id),
        )[:limit]

    async def add_reference(self, reference: MaterialReference) -> None:
        if any(
            existing.tenant_id == reference.tenant_id
            and existing.material_id == reference.material_id
            and existing.reference_type == reference.reference_type
            and existing.reference_id == reference.reference_id
            for existing in self.references
        ):
            raise MaterialReferenceAlreadyExistsError("素材引用已存在")
        self.references.append(reference)

    async def list_references(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
    ) -> list[MaterialReference]:
        return sorted(
            (
                reference
                for reference in self.references
                if reference.tenant_id == tenant_id and reference.material_id == material_id
            ),
            key=lambda reference: (reference.created_at, reference.id),
        )

    async def count_references(self, *, tenant_id: UUID, material_id: UUID) -> int:
        return sum(
            1
            for reference in self.references
            if reference.tenant_id == tenant_id and reference.material_id == material_id
        )

    async def add_download_task(self, task: DownloadTask) -> None:
        self.download_tasks[(task.tenant_id, task.id)] = task

    async def update_download_task(
        self,
        task: DownloadTask,
        *,
        expected_revision: int,
    ) -> bool:
        current = self.download_tasks.get((task.tenant_id, task.id))
        if current is None or current.revision != expected_revision:
            return False
        self.download_tasks[(task.tenant_id, task.id)] = task
        return True

    async def get_download_task(self, *, tenant_id: UUID, task_id: UUID) -> DownloadTask | None:
        return self.download_tasks.get((tenant_id, task_id))

    async def list_download_tasks(self, *, tenant_id: UUID) -> list[DownloadTask]:
        return [
            task
            for (stored_tenant_id, _), task in self.download_tasks.items()
            if stored_tenant_id == tenant_id
        ]


class MediaLibraryService:
    def __init__(
        self,
        *,
        repository: MaterialRepository,
        credential_issuer: MaterialUploadCredentialIssuer,
        object_verifier: MaterialObjectVerifier,
        object_cleaner: MaterialObjectCleaner | None = None,
        preview_issuer: MaterialPreviewUrlIssuer | None = None,
        audit_sink: MaterialAuditSink | None = None,
        artifact_source_resolver: ArtifactDownloadSourceResolver | None = None,
        clock: Callable[[], datetime] | None = None,
        upload_credential_ttl: timedelta = DEFAULT_UPLOAD_CREDENTIAL_TTL,
    ) -> None:
        if upload_credential_ttl <= timedelta(0) or upload_credential_ttl > timedelta(minutes=15):
            raise ValueError("material upload credentials must be short lived")
        self._repository = repository
        self._credential_issuer = credential_issuer
        self._object_verifier = object_verifier
        self._object_cleaner = object_cleaner
        self._preview_issuer = preview_issuer
        self._audit_sink = audit_sink
        self._artifact_source_resolver = artifact_source_resolver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._upload_credential_ttl = upload_credential_ttl

    @classmethod
    def in_memory(
        cls,
        *,
        credential_issuer: MaterialUploadCredentialIssuer,
        object_verifier: MaterialObjectVerifier,
        object_cleaner: MaterialObjectCleaner | None = None,
        preview_issuer: MaterialPreviewUrlIssuer | None = None,
        audit_sink: MaterialAuditSink | None = None,
        artifact_source_resolver: ArtifactDownloadSourceResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> MediaLibraryService:
        return cls(
            repository=InMemoryMaterialRepository(),
            credential_issuer=credential_issuer,
            object_verifier=object_verifier,
            object_cleaner=object_cleaner,
            preview_issuer=preview_issuer,
            audit_sink=audit_sink,
            artifact_source_resolver=artifact_source_resolver,
            clock=clock,
        )

    def _record_audit(
        self,
        *,
        action: str,
        tenant_id: UUID,
        actor_id: UUID,
        resource_id: UUID,
        details: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink.record(
            MediaLibraryAuditEvent(
                event_id=uuid4(),
                action=action,
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                resource_id=resource_id,
                occurred_at=self._clock(),
                details=details,
            )
        )

    async def create_folder(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        name: str,
        parent_id: UUID | None = None,
    ) -> MaterialFolder:
        if parent_id is not None:
            await self._ensure_folder_exists(tenant_id=tenant_id, folder_id=parent_id)
        folder = MaterialFolder.create(
            tenant_id=tenant_id,
            actor_id=actor_id,
            name=name,
            parent_id=parent_id,
            now=self._clock(),
        )
        await self._repository.add_folder(folder)
        return folder

    async def list_folders(
        self,
        *,
        tenant_id: UUID,
        parent_id: UUID | None = None,
    ) -> list[MaterialFolder]:
        if parent_id is not None:
            await self._ensure_folder_exists(tenant_id=tenant_id, folder_id=parent_id)
        return sorted(
            await self._repository.list_folders(tenant_id=tenant_id, parent_id=parent_id),
            key=lambda folder: folder.created_at,
        )

    async def request_upload_credentials(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        name: str,
        kind: MaterialKind,
        media_type: str,
        size_bytes: int,
        sha256: str,
        crc64ecma: str,
        tag_names: tuple[str, ...] = (),
        folder_id: UUID | None = None,
    ) -> MaterialUploadDraft:
        now = self._clock()
        expires_at = now + self._upload_credential_ttl
        if folder_id is not None:
            await self._ensure_folder_exists(tenant_id=tenant_id, folder_id=folder_id)
        # M-1 幂等：同 (tenant, sha256, size, folder) 的未过期草稿直接复用，
        # 审计失败 500 后客户端重试不会重复产生草稿；临时凭证不落库，重放重签。
        existing = await self._repository.find_active_upload_draft(
            tenant_id=tenant_id,
            sha256=sha256,
            size_bytes=size_bytes,
            folder_id=folder_id,
            now=now,
        )
        if existing is not None:
            refreshed = replace(existing, upload_expires_at=expires_at, updated_at=now)
            await self._repository.update_material(refreshed)
            credentials = await self._credential_issuer.issue_upload_credentials(
                tenant_id=tenant_id,
                key_prefix=self.key_prefix_for(refreshed),
                expires_at=expires_at,
                allowed_actions=MATERIAL_UPLOAD_ACTIONS,
            )
            self._record_audit(
                action="video.material.upload_requested",
                tenant_id=tenant_id,
                actor_id=actor_id,
                resource_id=refreshed.id,
                details=(
                    ("name", refreshed.name),
                    ("kind", refreshed.kind.value),
                    ("size_bytes", str(refreshed.size_bytes)),
                    ("replayed", "true"),
                ),
            )
            return MaterialUploadDraft(material=refreshed, credentials=credentials)
        material = Material.pending_upload(
            tenant_id=tenant_id,
            owner_id=actor_id,
            name=name,
            kind=kind,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            crc64ecma=crc64ecma,
            tag_names=tag_names,
            folder_id=folder_id,
            upload_expires_at=expires_at,
            now=now,
        )
        await self._repository.add_material(material)
        credentials = await self._credential_issuer.issue_upload_credentials(
            tenant_id=tenant_id,
            key_prefix=self.key_prefix_for(material),
            expires_at=expires_at,
            allowed_actions=MATERIAL_UPLOAD_ACTIONS,
        )
        self._record_audit(
            action="video.material.upload_requested",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=material.id,
            details=(
                ("name", material.name),
                ("kind", material.kind.value),
                ("size_bytes", str(material.size_bytes)),
            ),
        )
        return MaterialUploadDraft(material=material, credentials=credentials)

    async def get_material(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
        for_update: bool = False,
    ) -> Material:
        material = await self._repository.get_material(
            tenant_id=tenant_id,
            material_id=material_id,
            for_update=for_update,
        )
        if material is None or material.deleted_at is not None:
            raise MaterialNotFoundError("素材不存在")
        return material

    async def list_materials(self, *, tenant_id: UUID) -> list[Material]:
        return sorted(
            await self._repository.list_materials(tenant_id=tenant_id),
            key=lambda material: material.created_at,
        )

    async def request_preview_url(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
    ) -> IssuedMaterialPreview:
        material = await self.get_material(tenant_id=tenant_id, material_id=material_id)
        if material.status != "available":
            raise InvalidMaterialInput("素材尚不可预览")
        if self._preview_issuer is None:
            raise RuntimeError("素材预览服务未配置")
        return await self._preview_issuer.issue_preview_url(
            tenant_id=tenant_id,
            object_key=material.storage_key,
            expires_at=self._clock() + DEFAULT_PREVIEW_URL_TTL,
        )

    async def complete_upload(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        material_id: UUID,
    ) -> Material:
        material = await self.get_material(tenant_id=tenant_id, material_id=material_id)
        now = self._clock()
        if _as_utc(now) > _as_utc(material.upload_expires_at):
            failed = replace(
                material,
                status="upload_failed",
                cleanup_required=True,
                updated_at=now,
            )
            await self._repository.update_material(failed)
            raise UploadCredentialExpiredError("上传凭证已过期")
        try:
            stored_object = await self._object_verifier.inspect_uploaded_object(
                tenant_id=tenant_id,
                object_key=material.storage_key,
            )
        except MaterialObjectMissing:
            await self._repository.update_material(
                replace(
                    material,
                    status="upload_failed",
                    cleanup_required=True,
                    updated_at=now,
                )
            )
            raise InvalidMaterialInput("上传对象不存在或未完成直传") from None
        # 信任边界：只比对服务端可信值（size + COS 计算的 crc64ecma）。
        # sha256 是客户端写入的自定义元数据、可被恶意客户端伪造，不作为安全门禁。
        if (
            stored_object.size_bytes != material.size_bytes
            or not material.crc64ecma
            or stored_object.crc64ecma != material.crc64ecma
        ):
            await self._repository.update_material(
                replace(
                    material,
                    status="upload_failed",
                    cleanup_required=True,
                    updated_at=now,
                )
            )
            raise InvalidMaterialInput("上传对象与素材元数据不一致")
        completed = replace(
            material,
            status="available",
            cleanup_required=False,
            updated_at=now,
        )
        await self._repository.update_material(completed)
        self._record_audit(
            action="video.material.upload_completed",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=completed.id,
            details=(
                ("name", completed.name),
                ("status", completed.status),
            ),
        )
        return completed

    async def abort_upload(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        material_id: UUID,
        manage_all: bool = False,
    ) -> Material:
        material = await self.get_material(tenant_id=tenant_id, material_id=material_id)
        if not manage_all and material.owner_id != actor_id:
            raise MaterialNotFoundError("素材不存在")
        if material.status == "upload_failed" and material.cleanup_required:
            return material
        if material.status != "pending_upload":
            raise InvalidMaterialInput("只有待上传素材可以终止上传")
        now = self._clock()
        aborted = replace(
            material,
            status="upload_failed",
            cleanup_required=True,
            updated_at=now,
        )
        await self._repository.update_material(aborted)
        return aborted

    async def expire_upload_drafts(self, *, limit: int = 100) -> list[Material]:
        if limit <= 0 or limit > 1000:
            raise InvalidMaterialInput("过期扫描批次大小无效")
        now = self._clock()
        expired: list[Material] = []
        for material in await self._repository.list_expired_upload_drafts(
            now=now,
            limit=limit,
        ):
            failed = replace(
                material,
                status="upload_failed",
                cleanup_required=True,
                updated_at=now,
            )
            await self._repository.update_material(failed)
            expired.append(failed)
        return expired

    async def add_reference(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        material_id: UUID,
        reference_type: str,
        reference_id: UUID,
    ) -> MaterialReference:
        # L-2：锁定素材行使引用创建与删除在同一行锁上串行化。
        await self.get_material(tenant_id=tenant_id, material_id=material_id, for_update=True)
        reference = MaterialReference(
            id=uuid4(),
            tenant_id=tenant_id,
            material_id=material_id,
            reference_type=_validate_reference_type(reference_type),
            reference_id=reference_id,
            created_at=self._clock(),
        )
        await self._repository.add_reference(reference)
        self._record_audit(
            action="video.material.reference_created",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=material_id,
            details=(
                ("reference_type", reference.reference_type),
                ("reference_id", str(reference.reference_id)),
            ),
        )
        return reference

    async def list_references(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
    ) -> list[MaterialReference]:
        await self.get_material(tenant_id=tenant_id, material_id=material_id)
        return await self._repository.list_references(
            tenant_id=tenant_id,
            material_id=material_id,
        )

    async def delete_material(self, *, tenant_id: UUID, actor_id: UUID, material_id: UUID) -> None:
        # L-2：先对素材行加锁再检查引用，消除与 add_reference 的 TOCTOU 窗口。
        material = await self.get_material(
            tenant_id=tenant_id, material_id=material_id, for_update=True
        )
        if await self._repository.count_references(tenant_id=tenant_id, material_id=material_id):
            raise MaterialInUseError("素材仍被引用")
        await self._repository.update_material(
            replace(
                material,
                status="deleted",
                deleted_at=self._clock(),
                cleanup_required=True,
                updated_at=self._clock(),
            )
        )
        self._record_audit(
            action="video.material.deleted",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=material.id,
            details=(("name", material.name),),
        )

    async def cleanup_material_object(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
    ) -> Material:
        material = await self._repository.get_material(
            tenant_id=tenant_id,
            material_id=material_id,
        )
        if material is None:
            raise MaterialNotFoundError("素材不存在")
        if not material.cleanup_required:
            return material
        if self._object_cleaner is None:
            raise RuntimeError("素材对象清理器未配置")
        await self._object_cleaner.delete_object(
            tenant_id=tenant_id,
            object_key=material.storage_key,
        )
        cleaned = replace(
            material,
            cleanup_required=False,
            updated_at=self._clock(),
        )
        await self._repository.update_material(cleaned)
        return cleaned

    async def create_download_task(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        source_type: str,
        source_id: UUID,
    ) -> DownloadTask:
        if source_type == DOWNLOAD_SOURCE_MATERIAL:
            material = await self.get_material(tenant_id=tenant_id, material_id=source_id)
            if material.status != "available":
                raise InvalidMaterialInput("素材尚不可下载")
            total_bytes = material.size_bytes
        elif source_type == DOWNLOAD_SOURCE_ARTIFACT:
            if self._artifact_source_resolver is None:
                raise RuntimeError("成片下载来源解析器未配置")
            source = await self._artifact_source_resolver.resolve_artifact(
                tenant_id=tenant_id,
                artifact_id=source_id,
            )
            if source is None:
                raise MaterialNotFoundError("成片不存在")
            total_bytes = source.size_bytes
        else:
            raise InvalidMaterialInput("下载来源类型无效")
        now = self._clock()
        task = DownloadTask(
            id=uuid4(),
            tenant_id=tenant_id,
            requested_by=actor_id,
            source_type=source_type,
            source_id=source_id,
            status=DownloadTaskStatus.QUEUED,
            progress=0,
            downloaded_bytes=0,
            total_bytes=total_bytes,
            resume_token=None,
            error_code=None,
            retryable=False,
            retry_count=0,
            revision=0,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        await self._repository.add_download_task(task)
        self._record_audit(
            action="video.download_task.created",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=task.id,
            details=(
                ("source_type", task.source_type),
                ("source_id", str(task.source_id)),
            ),
        )
        return task

    async def start_download_task(self, *, tenant_id: UUID, task_id: UUID) -> DownloadTask:
        task = await self._get_download_task(tenant_id=tenant_id, task_id=task_id)
        if task.status is not DownloadTaskStatus.QUEUED:
            raise InvalidDownloadTaskTransition("只有排队中的下载任务可以开始")
        return await self._update_download_task(
            replace(task, status=DownloadTaskStatus.RUNNING, updated_at=self._clock())
        )

    async def update_download_progress(
        self,
        *,
        tenant_id: UUID,
        task_id: UUID,
        downloaded_bytes: int,
        resume_token: str | None,
    ) -> DownloadTask:
        task = await self._get_download_task(tenant_id=tenant_id, task_id=task_id)
        if task.status is not DownloadTaskStatus.RUNNING:
            raise InvalidDownloadTaskTransition("只有运行中的下载任务可以更新进度")
        if downloaded_bytes < task.downloaded_bytes:
            raise InvalidMaterialInput("下载进度不能回退")
        if downloaded_bytes < 0 or downloaded_bytes > task.total_bytes:
            raise InvalidMaterialInput("下载字节数超出任务范围")
        if resume_token is not None and len(resume_token) > MAX_RESUME_TOKEN_LENGTH:
            raise InvalidMaterialInput("下载断点令牌过长")
        progress = int((downloaded_bytes / task.total_bytes) * 100) if task.total_bytes else 0
        return await self._update_download_task(
            replace(
                task,
                downloaded_bytes=downloaded_bytes,
                progress=progress,
                resume_token=resume_token,
                updated_at=self._clock(),
            )
        )

    async def fail_download_task(
        self,
        *,
        tenant_id: UUID,
        task_id: UUID,
        error_code: str,
        retryable: bool,
    ) -> DownloadTask:
        task = await self._get_download_task(tenant_id=tenant_id, task_id=task_id)
        if task.status is not DownloadTaskStatus.RUNNING:
            raise InvalidDownloadTaskTransition("只有运行中的下载任务可以失败")
        if _DOWNLOAD_ERROR_CODE_RE.fullmatch(error_code) is None:
            raise InvalidMaterialInput("下载错误码格式无效")
        now = self._clock()
        return await self._update_download_task(
            replace(
                task,
                status=DownloadTaskStatus.FAILED,
                error_code=error_code,
                retryable=retryable,
                updated_at=now,
                completed_at=now,
            )
        )

    async def complete_download_task(
        self,
        *,
        tenant_id: UUID,
        task_id: UUID,
    ) -> DownloadTask:
        task = await self._get_download_task(tenant_id=tenant_id, task_id=task_id)
        if task.status is not DownloadTaskStatus.RUNNING:
            raise InvalidDownloadTaskTransition("只有运行中的下载任务可以完成")
        now = self._clock()
        return await self._update_download_task(
            replace(
                task,
                status=DownloadTaskStatus.SUCCEEDED,
                progress=100,
                downloaded_bytes=task.total_bytes,
                resume_token=None,
                error_code=None,
                retryable=False,
                updated_at=now,
                completed_at=now,
            )
        )

    async def cancel_download_task(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        manage_all: bool = False,
    ) -> DownloadTask:
        task = await self._get_download_task(tenant_id=tenant_id, task_id=task_id)
        if not manage_all and task.requested_by != actor_id:
            raise MaterialNotFoundError("下载任务不存在")
        if task.status not in {DownloadTaskStatus.QUEUED, DownloadTaskStatus.RUNNING}:
            raise InvalidDownloadTaskTransition("只有排队中或运行中的下载任务可以取消")
        now = self._clock()
        cancelled = await self._update_download_task(
            replace(
                task,
                status=DownloadTaskStatus.CANCELLED,
                retryable=False,
                updated_at=now,
                completed_at=now,
            )
        )
        self._record_audit(
            action="video.download_task.cancelled",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=cancelled.id,
            details=(("source_type", cancelled.source_type),),
        )
        return cancelled

    async def retry_download_task(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        manage_all: bool = False,
    ) -> DownloadTask:
        task = await self._get_download_task(tenant_id=tenant_id, task_id=task_id)
        if not manage_all and task.requested_by != actor_id:
            raise MaterialNotFoundError("下载任务不存在")
        if task.status is not DownloadTaskStatus.FAILED or not task.retryable:
            raise InvalidDownloadTaskTransition("只有可重试的失败任务可以重试")
        retried = await self._update_download_task(
            replace(
                task,
                requested_by=actor_id,
                status=DownloadTaskStatus.QUEUED,
                retry_count=task.retry_count + 1,
                error_code=None,
                retryable=False,
                updated_at=self._clock(),
                completed_at=None,
            )
        )
        self._record_audit(
            action="video.download_task.retried",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource_id=retried.id,
            details=(("retry_count", str(retried.retry_count)),),
        )
        return retried

    async def list_download_tasks(self, *, tenant_id: UUID) -> list[DownloadTask]:
        return sorted(
            await self._repository.list_download_tasks(tenant_id=tenant_id),
            key=lambda task: task.created_at,
        )

    async def _get_download_task(self, *, tenant_id: UUID, task_id: UUID) -> DownloadTask:
        task = await self._repository.get_download_task(tenant_id=tenant_id, task_id=task_id)
        if task is None:
            raise MaterialNotFoundError("下载任务不存在")
        return task

    async def _update_download_task(self, task: DownloadTask) -> DownloadTask:
        updated = replace(task, revision=task.revision + 1)
        if not await self._repository.update_download_task(
            updated,
            expected_revision=task.revision,
        ):
            raise DownloadTaskConcurrentUpdateError("下载任务已被并发更新，请刷新后重试")
        return updated

    async def _ensure_folder_exists(self, *, tenant_id: UUID, folder_id: UUID) -> MaterialFolder:
        folder = await self._repository.get_folder(tenant_id=tenant_id, folder_id=folder_id)
        if folder is None:
            raise MaterialFolderNotFoundError("素材文件夹不存在")
        return folder

    @staticmethod
    def key_prefix_for(material: Material) -> str:
        return f"materials/{material.tenant_id}/{material.id}/"


def _validate_material_input(
    *,
    name: str,
    kind: MaterialKind,
    media_type: str,
    size_bytes: int,
    sha256: str,
    crc64ecma: str,
) -> str:
    normalized = name.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 for character in normalized)
        or len(normalized) > 255
    ):
        raise InvalidMaterialInput("素材文件名无效")
    if media_type not in _MEDIA_EXTENSIONS:
        raise InvalidMaterialInput("素材类型不支持")
    if kind is MaterialKind.VIDEO and not media_type.startswith("video/"):
        raise InvalidMaterialInput("视频素材类型不匹配")
    if kind is MaterialKind.IMAGE and not media_type.startswith("image/"):
        raise InvalidMaterialInput("图片素材类型不匹配")
    if kind is MaterialKind.MUSIC and not media_type.startswith("audio/"):
        raise InvalidMaterialInput("音乐素材类型不匹配")
    if PurePosixPath(normalized).suffix.lower() not in _MEDIA_EXTENSIONS[media_type]:
        raise InvalidMaterialInput("素材扩展名与媒体类型不一致")
    if size_bytes <= 0 or size_bytes > MAX_MATERIAL_SIZE_BYTES:
        raise InvalidMaterialInput("素材大小无效")
    if _SHA256_RE.fullmatch(sha256) is None:
        raise InvalidMaterialInput("素材摘要无效")
    if _CRC64_RE.fullmatch(crc64ecma) is None or int(crc64ecma) > _MAX_UINT64:
        raise InvalidMaterialInput("素材 CRC64 无效")
    return normalized


def _validate_folder_name(name: str) -> str:
    normalized = name.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 for character in normalized)
        or len(normalized) > 255
    ):
        raise InvalidMaterialInput("素材文件夹名称无效")
    return normalized


def _normalize_tags(tag_names: tuple[str, ...]) -> tuple[str, ...]:
    tags = {
        tag.strip()
        for tag in tag_names
        if tag.strip() and len(tag.strip()) <= 64 and "/" not in tag and "\\" not in tag
    }
    return tuple(sorted(tags))


def _validate_reference_type(reference_type: str) -> str:
    value = reference_type.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", value):
        raise InvalidMaterialInput("素材引用类型无效")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
