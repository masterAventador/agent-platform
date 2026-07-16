from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.capabilities.video_studio.media_library import (
    DownloadTask,
    DownloadTaskStatus,
    Material,
    MaterialFolder,
    MaterialKind,
    MaterialReference,
    MaterialReferenceAlreadyExistsError,
    ResolvedDownloadSource,
)
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.artifacts import ArtifactRecord


class VideoMaterialFolderRecord(Base):
    __tablename__ = "video_material_folders"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["video_material_folders.tenant_id", "video_material_folders.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_video_material_folders_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "parent_id",
            "name",
            name="uq_video_material_folders_sibling_name",
        ),
    )


class VideoMaterialRecord(Base):
    __tablename__ = "video_materials"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    folder_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(700), unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    tag_names: Mapped[str] = mapped_column(String(2000), default="[]")
    upload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleanup_required: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "folder_id"],
            ["video_material_folders.tenant_id", "video_material_folders.id"],
            ondelete="SET NULL",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_video_materials_tenant_id_id"),
        CheckConstraint("kind IN ('video', 'image', 'music')", name="ck_video_material_kind"),
        CheckConstraint(
            "status IN ('pending_upload', 'available', 'upload_failed', 'deleted')",
            name="ck_video_material_status",
        ),
    )


class VideoMaterialReferenceRecord(Base):
    __tablename__ = "video_material_references"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    material_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    reference_type: Mapped[str] = mapped_column(String(64))
    reference_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "material_id"],
            ["video_materials.tenant_id", "video_materials.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "material_id",
            "reference_type",
            "reference_id",
            name="uq_video_material_reference_target",
        ),
    )


class VideoDownloadTaskRecord(Base):
    __tablename__ = "video_download_tasks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    requested_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    progress: Mapped[int] = mapped_column(BigInteger)
    downloaded_bytes: Mapped[int] = mapped_column(BigInteger)
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    resume_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(BigInteger, default=0)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_video_download_tasks_tenant_id_id"),
        CheckConstraint(
            "source_type IN ('material', 'artifact')",
            name="ck_video_download_task_source_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_video_download_task_status",
        ),
        CheckConstraint("revision >= 0", name="ck_video_download_task_revision"),
    )


class SqlAlchemyMediaLibraryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_folder(self, folder: MaterialFolder) -> None:
        self._session.add(VideoMaterialFolderRecord(**asdict(folder)))

    async def get_folder(self, *, tenant_id: UUID, folder_id: UUID) -> MaterialFolder | None:
        record = await self._session.get(VideoMaterialFolderRecord, folder_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return _record_to_folder(record)

    async def list_folders(
        self,
        *,
        tenant_id: UUID,
        parent_id: UUID | None = None,
    ) -> list[MaterialFolder]:
        query = select(VideoMaterialFolderRecord).where(
            VideoMaterialFolderRecord.tenant_id == tenant_id
        )
        if parent_id is not None:
            query = query.where(VideoMaterialFolderRecord.parent_id == parent_id)
        records = (
            await self._session.execute(
                query.order_by(
                    VideoMaterialFolderRecord.created_at,
                    VideoMaterialFolderRecord.id,
                )
            )
        ).scalars()
        return [_record_to_folder(record) for record in records]

    async def add_material(self, material: Material) -> None:
        self._session.add(VideoMaterialRecord(**_material_to_record_values(material)))

    async def update_material(self, material: Material) -> None:
        record = await self._session.get(VideoMaterialRecord, material.id)
        if record is None or record.tenant_id != material.tenant_id:
            raise RuntimeError("video material disappeared during update")
        for key, value in _material_to_record_values(material).items():
            setattr(record, key, value)

    async def get_material(self, *, tenant_id: UUID, material_id: UUID) -> Material | None:
        record = await self._session.get(VideoMaterialRecord, material_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return _record_to_material(record)

    async def list_materials(self, *, tenant_id: UUID) -> list[Material]:
        records = (
            await self._session.execute(
                select(VideoMaterialRecord)
                .where(
                    VideoMaterialRecord.tenant_id == tenant_id,
                    VideoMaterialRecord.deleted_at.is_(None),
                )
                .order_by(VideoMaterialRecord.created_at, VideoMaterialRecord.id)
            )
        ).scalars()
        return [_record_to_material(record) for record in records]

    async def list_expired_upload_drafts(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[Material]:
        records = (
            await self._session.execute(
                select(VideoMaterialRecord)
                .where(
                    VideoMaterialRecord.status == "pending_upload",
                    VideoMaterialRecord.upload_expires_at < now,
                )
                .order_by(VideoMaterialRecord.upload_expires_at, VideoMaterialRecord.id)
                .limit(limit)
            )
        ).scalars()
        return [_record_to_material(record) for record in records]

    async def add_reference(self, reference: MaterialReference) -> None:
        self._session.add(VideoMaterialReferenceRecord(**asdict(reference)))
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise MaterialReferenceAlreadyExistsError("素材引用已存在") from error

    async def list_references(
        self,
        *,
        tenant_id: UUID,
        material_id: UUID,
    ) -> list[MaterialReference]:
        records = (
            await self._session.execute(
                select(VideoMaterialReferenceRecord)
                .where(
                    VideoMaterialReferenceRecord.tenant_id == tenant_id,
                    VideoMaterialReferenceRecord.material_id == material_id,
                )
                .order_by(
                    VideoMaterialReferenceRecord.created_at,
                    VideoMaterialReferenceRecord.id,
                )
            )
        ).scalars()
        return [_record_to_reference(record) for record in records]

    async def count_references(self, *, tenant_id: UUID, material_id: UUID) -> int:
        return len(
            (
                await self._session.execute(
                    select(VideoMaterialReferenceRecord.id).where(
                        VideoMaterialReferenceRecord.tenant_id == tenant_id,
                        VideoMaterialReferenceRecord.material_id == material_id,
                    )
                )
            ).all()
        )

    async def add_download_task(self, task: DownloadTask) -> None:
        self._session.add(VideoDownloadTaskRecord(**_download_task_to_record_values(task)))

    async def update_download_task(
        self,
        task: DownloadTask,
        *,
        expected_revision: int,
    ) -> bool:
        result = await self._session.execute(
            update(VideoDownloadTaskRecord)
            .where(
                VideoDownloadTaskRecord.id == task.id,
                VideoDownloadTaskRecord.tenant_id == task.tenant_id,
                VideoDownloadTaskRecord.revision == expected_revision,
            )
            .values(**_download_task_to_record_values(task))
        )
        rowcount = cast(int, result.rowcount)  # type: ignore[attr-defined]
        return rowcount == 1

    async def get_download_task(self, *, tenant_id: UUID, task_id: UUID) -> DownloadTask | None:
        record = await self._session.get(VideoDownloadTaskRecord, task_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return _record_to_download_task(record)

    async def list_download_tasks(self, *, tenant_id: UUID) -> list[DownloadTask]:
        records = (
            await self._session.execute(
                select(VideoDownloadTaskRecord)
                .where(VideoDownloadTaskRecord.tenant_id == tenant_id)
                .order_by(VideoDownloadTaskRecord.created_at, VideoDownloadTaskRecord.id)
            )
        ).scalars()
        return [_record_to_download_task(record) for record in records]


def _record_to_folder(record: VideoMaterialFolderRecord) -> MaterialFolder:
    return MaterialFolder(
        id=record.id,
        tenant_id=record.tenant_id,
        parent_id=record.parent_id,
        name=record.name,
        created_by=record.created_by,
        created_at=record.created_at,
    )


def _material_to_record_values(material: Material) -> dict[str, object]:
    values = asdict(material)
    values["kind"] = material.kind.value
    values["tag_names"] = json.dumps(list(material.tags), ensure_ascii=False)
    values.pop("tags")
    return values


def _record_to_material(record: VideoMaterialRecord) -> Material:
    return Material(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_id=record.owner_id,
        folder_id=record.folder_id,
        name=record.name,
        kind=MaterialKind(record.kind),
        media_type=record.media_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        storage_key=record.storage_key,
        status=record.status,
        tags=tuple(json.loads(record.tag_names or "[]")),
        upload_expires_at=record.upload_expires_at,
        cleanup_required=record.cleanup_required,
        artifact_id=record.artifact_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        deleted_at=record.deleted_at,
    )


def _record_to_reference(record: VideoMaterialReferenceRecord) -> MaterialReference:
    return MaterialReference(
        id=record.id,
        tenant_id=record.tenant_id,
        material_id=record.material_id,
        reference_type=record.reference_type,
        reference_id=record.reference_id,
        created_at=record.created_at,
    )


def _download_task_to_record_values(task: DownloadTask) -> dict[str, object]:
    values = asdict(task)
    values["status"] = task.status.value
    return values


def _record_to_download_task(record: VideoDownloadTaskRecord) -> DownloadTask:
    return DownloadTask(
        id=record.id,
        tenant_id=record.tenant_id,
        requested_by=record.requested_by,
        source_type=record.source_type,
        source_id=record.source_id,
        status=DownloadTaskStatus(record.status),
        progress=record.progress,
        downloaded_bytes=record.downloaded_bytes,
        total_bytes=record.total_bytes,
        resume_token=record.resume_token,
        error_code=record.error_code,
        retryable=record.retryable,
        retry_count=record.retry_count,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


class SqlAlchemyArtifactDownloadSourceResolver:
    """成片（Core Artifact）下载来源解析：按租户读取可信产物元数据。

    只依赖 Core 公开的产物存储模型；不读取产物内容，也不放宽租户隔离。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_artifact(
        self,
        *,
        tenant_id: UUID,
        artifact_id: UUID,
    ) -> ResolvedDownloadSource | None:
        record = (
            await self._session.execute(
                select(ArtifactRecord).where(
                    ArtifactRecord.tenant_id == tenant_id,
                    ArtifactRecord.id == artifact_id,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        return ResolvedDownloadSource(size_bytes=record.size_bytes)
