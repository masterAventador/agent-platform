from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    delete,
    select,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.artifacts.entities import Artifact, File, TaskAttachment


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_files_tenant_id_id"),
    )


class TaskAttachmentRecord(Base):
    __tablename__ = "task_attachments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    workspace_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "file_id"], ["files.tenant_id", "files.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "tenant_id", "run_id", "workspace_path", name="uq_task_attachment_workspace_path"
        ),
    )


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["runs.tenant_id", "runs.id"], ondelete="CASCADE"
        ),
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, file: File) -> None:
        self._session.add(FileRecord(**asdict(file)))
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, file_id: UUID) -> File | None:
        record = (
            await self._session.execute(
                select(FileRecord).where(
                    FileRecord.tenant_id == tenant_id, FileRecord.id == file_id
                )
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        return File(
            id=record.id,
            tenant_id=record.tenant_id,
            owner_id=record.owner_id,
            name=record.name,
            media_type=record.media_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            storage_key=record.storage_key,
            created_at=_utc(record.created_at),
        )

    async def delete(self, *, tenant_id: UUID, file_id: UUID) -> bool:
        result = await self._session.execute(
            delete(FileRecord).where(
                FileRecord.tenant_id == tenant_id, FileRecord.id == file_id
            )
        )
        return isinstance(result, CursorResult) and bool(result.rowcount)


class SqlAlchemyTaskAttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, attachment: TaskAttachment) -> None:
        self._session.add(TaskAttachmentRecord(**asdict(attachment)))
        await self._session.flush()

    async def list_for_run(self, *, tenant_id: UUID, run_id: UUID) -> list[TaskAttachment]:
        records = (
            await self._session.execute(
                select(TaskAttachmentRecord)
                .where(
                    TaskAttachmentRecord.tenant_id == tenant_id,
                    TaskAttachmentRecord.run_id == run_id,
                )
                .order_by(TaskAttachmentRecord.created_at, TaskAttachmentRecord.id)
            )
        ).scalars()
        return [
            TaskAttachment(
                id=record.id,
                tenant_id=record.tenant_id,
                run_id=record.run_id,
                file_id=record.file_id,
                workspace_path=record.workspace_path,
                created_at=_utc(record.created_at),
            )
            for record in records
        ]


class SqlAlchemyArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, artifact: Artifact) -> None:
        self._session.add(ArtifactRecord(**asdict(artifact)))
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, artifact_id: UUID) -> Artifact | None:
        record = (
            await self._session.execute(
                select(ArtifactRecord).where(
                    ArtifactRecord.tenant_id == tenant_id,
                    ArtifactRecord.id == artifact_id,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list_for_run(self, *, tenant_id: UUID, run_id: UUID) -> list[Artifact]:
        records = (
            await self._session.execute(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.tenant_id == tenant_id,
                    ArtifactRecord.run_id == run_id,
                )
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
            )
        ).scalars()
        return [self._to_entity(record) for record in records]

    async def delete(self, *, tenant_id: UUID, artifact_id: UUID) -> bool:
        result = await self._session.execute(
            delete(ArtifactRecord).where(
                ArtifactRecord.tenant_id == tenant_id,
                ArtifactRecord.id == artifact_id,
            )
        )
        return isinstance(result, CursorResult) and bool(result.rowcount)

    @staticmethod
    def _to_entity(record: ArtifactRecord) -> Artifact:
        return Artifact(
            id=record.id,
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            created_by=record.created_by,
            name=record.name,
            media_type=record.media_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            storage_key=record.storage_key,
            created_at=_utc(record.created_at),
        )
