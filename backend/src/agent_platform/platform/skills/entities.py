from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from agent_platform.platform.skills.bundle import SkillBundle
from agent_platform.platform.skills.security import (
    SkillReviewStatus,
    SkillSecurityFinding,
    audit_skill_bundle,
)


class SkillLifecycleStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class SkillUsage:
    employee_id: UUID
    employee_name: str
    relation: str
    version: int | None = None


@dataclass(frozen=True)
class Skill:
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    latest_version: int
    published_version: int | None
    lifecycle_status: SkillLifecycleStatus
    source: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    deleted_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        created_by: UUID,
        bundle: SkillBundle,
        source: str = "uploaded",
    ) -> "Skill":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=bundle.name,
            description=bundle.description,
            latest_version=1,
            published_version=None,
            lifecycle_status=SkillLifecycleStatus.DRAFT,
            source=source,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def add_version(self, bundle: SkillBundle) -> "Skill":
        return replace(
            self,
            description=bundle.description,
            latest_version=self.latest_version + 1,
            updated_at=datetime.now(UTC),
        )

    def publish(self, version: int) -> "Skill":
        return replace(
            self,
            published_version=version,
            lifecycle_status=SkillLifecycleStatus.PUBLISHED,
            archived_at=None,
            updated_at=datetime.now(UTC),
        )

    def offline(self) -> "Skill":
        now = datetime.now(UTC)
        return replace(
            self,
            published_version=None,
            lifecycle_status=SkillLifecycleStatus.ARCHIVED,
            archived_at=now,
            updated_at=now,
        )

    def mark_deleted(self) -> "Skill":
        now = datetime.now(UTC)
        return replace(
            self,
            published_version=None,
            lifecycle_status=SkillLifecycleStatus.DELETED,
            deleted_at=now,
            updated_at=now,
        )


@dataclass(frozen=True)
class SkillVersion:
    id: UUID
    skill_id: UUID
    tenant_id: UUID
    version: int
    description: str
    digest: str
    files: list[str]
    storage_key: str
    review_status: SkillReviewStatus
    security_findings: list[SkillSecurityFinding]
    reviewed_at: datetime
    created_by: UUID
    created_at: datetime
    published_at: datetime | None

    @classmethod
    def create(
        cls,
        *,
        skill: Skill,
        version: int,
        bundle: SkillBundle,
        storage_key: str,
        created_by: UUID,
    ) -> "SkillVersion":
        review = audit_skill_bundle(bundle)
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            skill_id=skill.id,
            tenant_id=skill.tenant_id,
            version=version,
            description=bundle.description,
            digest=bundle.digest,
            files=bundle.files,
            storage_key=storage_key,
            review_status=review.status,
            security_findings=review.findings,
            reviewed_at=now,
            created_by=created_by,
            created_at=now,
            published_at=None,
        )

    def publish(self) -> "SkillVersion":
        return replace(self, published_at=datetime.now(UTC))
