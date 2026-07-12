from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from agent_platform.platform.skills.bundle import SkillBundle


@dataclass(frozen=True)
class Skill:
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    latest_version: int
    published_version: int | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        created_by: UUID,
        bundle: SkillBundle,
    ) -> "Skill":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=bundle.name,
            description=bundle.description,
            latest_version=1,
            published_version=None,
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
            updated_at=datetime.now(UTC),
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
        return cls(
            id=uuid4(),
            skill_id=skill.id,
            tenant_id=skill.tenant_id,
            version=version,
            description=bundle.description,
            digest=bundle.digest,
            files=bundle.files,
            storage_key=storage_key,
            created_by=created_by,
            created_at=datetime.now(UTC),
            published_at=None,
        )

    def publish(self) -> "SkillVersion":
        return replace(self, published_at=datetime.now(UTC))
