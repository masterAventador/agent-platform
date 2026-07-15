from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Uuid, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
)
from agent_platform.platform.skills.entities import (
    Skill,
    SkillLifecycleStatus,
    SkillUsage,
    SkillVersion,
)
from agent_platform.platform.skills.errors import SkillNameAlreadyExists
from agent_platform.platform.skills.security import (
    SkillReviewStatus,
    findings_from_json,
    findings_to_json,
)


class SkillRecord(Base):
    __tablename__ = "skills"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(1024))
    latest_version: Mapped[int] = mapped_column(Integer)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="draft")
    source: Mapped[str] = mapped_column(String(32), default="uploaded")
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("uq_skills_tenant_name", tenant_id, name, unique=True),)


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    skill_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(1024))
    digest: Mapped[str] = mapped_column(String(64))
    files: Mapped[list[str]] = mapped_column(JSON)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    review_status: Mapped[str] = mapped_column(String(32), default="approved")
    security_findings: Mapped[list[dict[str, str | None]]] = mapped_column(JSON, default=list)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("uq_skill_versions_number", skill_id, version, unique=True),)


class SqlAlchemySkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, skill: Skill, version: SkillVersion) -> None:
        self._session.add(self._skill_record(skill))
        self._session.add(self._version_record(version))
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise SkillNameAlreadyExists from error

    async def update(self, skill: Skill) -> None:
        record = await self._session.get(SkillRecord, skill.id)
        if record is None or record.tenant_id != skill.tenant_id:
            return
        record.description = skill.description
        record.latest_version = skill.latest_version
        record.published_version = skill.published_version
        record.lifecycle_status = skill.lifecycle_status.value
        record.source = skill.source
        record.updated_at = skill.updated_at
        record.archived_at = skill.archived_at
        record.deleted_at = skill.deleted_at
        await self._session.flush()

    async def add_version(self, version: SkillVersion) -> None:
        self._session.add(self._version_record(version))
        await self._session.flush()

    async def update_version(self, version: SkillVersion) -> None:
        record = await self._session.get(SkillVersionRecord, version.id)
        if record is not None and record.tenant_id == version.tenant_id:
            record.published_at = version.published_at
            record.review_status = version.review_status.value
            record.security_findings = findings_to_json(version.security_findings)
            record.reviewed_at = version.reviewed_at
            await self._session.flush()

    async def get(self, *, tenant_id: UUID, skill_id: UUID) -> Skill | None:
        result = await self._session.execute(
            select(SkillRecord).where(
                SkillRecord.id == skill_id,
                SkillRecord.tenant_id == tenant_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._skill_entity(record) if record is not None else None

    async def get_by_name(self, *, tenant_id: UUID, name: str) -> Skill | None:
        result = await self._session.execute(
            select(SkillRecord).where(
                SkillRecord.tenant_id == tenant_id,
                SkillRecord.name == name,
                SkillRecord.lifecycle_status != SkillLifecycleStatus.DELETED.value,
            )
        )
        record = result.scalar_one_or_none()
        return self._skill_entity(record) if record is not None else None

    async def list_all(self, *, tenant_id: UUID) -> list[Skill]:
        result = await self._session.execute(
            select(SkillRecord)
            .where(SkillRecord.tenant_id == tenant_id)
            .where(SkillRecord.lifecycle_status != SkillLifecycleStatus.DELETED.value)
            .order_by(SkillRecord.created_at)
        )
        return [self._skill_entity(record) for record in result.scalars()]

    async def are_bindable(self, *, tenant_id: UUID, skill_ids: list[UUID]) -> bool:
        versions = await self.published_versions(tenant_id=tenant_id, skill_ids=skill_ids)
        return len(versions) == len(set(skill_ids))

    async def published_versions(
        self,
        *,
        tenant_id: UUID,
        skill_ids: list[UUID],
    ) -> dict[UUID, int]:
        unique_ids = set(skill_ids)
        if not unique_ids:
            return {}
        result = await self._session.execute(
            select(SkillRecord).where(
                SkillRecord.tenant_id == tenant_id,
                SkillRecord.id.in_(unique_ids),
                SkillRecord.published_version.is_not(None),
                SkillRecord.lifecycle_status == SkillLifecycleStatus.PUBLISHED.value,
            )
        )
        published: dict[UUID, int] = {}
        for record in result.scalars():
            if record.published_version is None:
                continue
            version = await self.get_version(
                tenant_id=tenant_id,
                skill_id=record.id,
                version=record.published_version,
            )
            if version is not None and version.review_status is SkillReviewStatus.APPROVED:
                published[record.id] = record.published_version
        return published

    async def list_usage(self, *, tenant_id: UUID, skill_id: UUID) -> list[SkillUsage]:
        skill_id_text = str(skill_id)
        items: list[SkillUsage] = []
        drafts = await self._session.execute(
            select(EmployeeRecord).where(EmployeeRecord.tenant_id == tenant_id)
        )
        for employee in drafts.scalars():
            if skill_id_text in employee.skill_ids:
                items.append(
                    SkillUsage(
                        employee_id=employee.id,
                        employee_name=employee.name,
                        relation="employee_draft",
                    )
                )
        versions = await self._session.execute(
            select(EmployeeVersionRecord).where(EmployeeVersionRecord.tenant_id == tenant_id)
        )
        for version in versions.scalars():
            relation_version = _definition_skill_version(version.definition, skill_id_text)
            if relation_version is not None:
                items.append(
                    SkillUsage(
                        employee_id=version.employee_id,
                        employee_name=str(version.definition.get("name") or "未命名员工"),
                        relation="employee_version",
                        version=relation_version,
                    )
                )
        return items

    async def get_version(
        self, *, tenant_id: UUID, skill_id: UUID, version: int
    ) -> SkillVersion | None:
        result = await self._session.execute(
            select(SkillVersionRecord).where(
                SkillVersionRecord.tenant_id == tenant_id,
                SkillVersionRecord.skill_id == skill_id,
                SkillVersionRecord.version == version,
            )
        )
        record = result.scalar_one_or_none()
        return self._version_entity(record) if record is not None else None

    async def list_versions(self, *, tenant_id: UUID, skill_id: UUID) -> list[SkillVersion]:
        result = await self._session.execute(
            select(SkillVersionRecord)
            .where(
                SkillVersionRecord.tenant_id == tenant_id,
                SkillVersionRecord.skill_id == skill_id,
            )
            .order_by(SkillVersionRecord.version.desc())
        )
        return [self._version_entity(record) for record in result.scalars()]

    @staticmethod
    def _skill_record(skill: Skill) -> SkillRecord:
        return SkillRecord(
            id=skill.id, tenant_id=skill.tenant_id, name=skill.name,
            description=skill.description, latest_version=skill.latest_version,
            published_version=skill.published_version,
            lifecycle_status=skill.lifecycle_status.value, source=skill.source,
            created_by=skill.created_by, created_at=skill.created_at,
            updated_at=skill.updated_at, archived_at=skill.archived_at,
            deleted_at=skill.deleted_at,
        )

    @staticmethod
    def _version_record(version: SkillVersion) -> SkillVersionRecord:
        return SkillVersionRecord(
            id=version.id, skill_id=version.skill_id, tenant_id=version.tenant_id,
            version=version.version, description=version.description, digest=version.digest,
            files=version.files, storage_key=version.storage_key,
            review_status=version.review_status.value,
            security_findings=findings_to_json(version.security_findings),
            reviewed_at=version.reviewed_at,
            created_by=version.created_by, created_at=version.created_at,
            published_at=version.published_at,
        )

    @classmethod
    def _skill_entity(cls, record: SkillRecord) -> Skill:
        return Skill(
            id=record.id, tenant_id=record.tenant_id, name=record.name,
            description=record.description, latest_version=record.latest_version,
            published_version=record.published_version,
            lifecycle_status=SkillLifecycleStatus(record.lifecycle_status),
            source=record.source, created_by=record.created_by,
            created_at=cls._as_utc(record.created_at), updated_at=cls._as_utc(record.updated_at),
            archived_at=cls._as_utc(record.archived_at) if record.archived_at else None,
            deleted_at=cls._as_utc(record.deleted_at) if record.deleted_at else None,
        )

    @classmethod
    def _version_entity(cls, record: SkillVersionRecord) -> SkillVersion:
        return SkillVersion(
            id=record.id, skill_id=record.skill_id, tenant_id=record.tenant_id,
            version=record.version, description=record.description, digest=record.digest,
            files=record.files, storage_key=record.storage_key, created_by=record.created_by,
            review_status=SkillReviewStatus(record.review_status),
            security_findings=findings_from_json(record.security_findings),
            reviewed_at=cls._as_utc(record.reviewed_at),
            created_at=cls._as_utc(record.created_at),
            published_at=cls._as_utc(record.published_at) if record.published_at else None,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _definition_skill_version(definition: dict[str, object], skill_id: str) -> int | None:
    version_refs = definition.get("skill_versions")
    if isinstance(version_refs, list):
        for value in version_refs:
            if not isinstance(value, dict) or value.get("skill_id") != skill_id:
                continue
            version = value.get("version")
            return int(version) if isinstance(version, int) else None
    skill_ids = definition.get("skill_ids")
    if isinstance(skill_ids, list) and skill_id in skill_ids:
        return 0
    return None
