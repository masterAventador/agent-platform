from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class RuntimeType(StrEnum):
    AUTONOMOUS = "autonomous"
    WORKFLOW = "workflow"
    HYBRID = "hybrid"


class EmployeeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class EmployeeVisibility(StrEnum):
    PRIVATE = "private"
    TENANT = "tenant"


@dataclass(frozen=True, slots=True)
class EmployeeDraft:
    name: str
    avatar_url: str | None
    role_description: str
    visibility: EmployeeVisibility
    runtime_type: RuntimeType
    system_prompt: str
    model_settings: dict[str, object]
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    capabilities: dict[str, bool]
    skill_ids: list[UUID]
    tool_ids: list[UUID]
    knowledge_base_ids: list[UUID]
    approval_policy: dict[str, object]
    release_strategy: dict[str, object]

    def normalized(self) -> "EmployeeDraft":
        return replace(
            self,
            name=self.name.strip(),
            role_description=self.role_description.strip(),
            system_prompt=self.system_prompt.strip(),
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "name": self.name,
            "avatar_url": self.avatar_url,
            "role_description": self.role_description,
            "visibility": self.visibility.value,
            "work_mode": self.runtime_type.value,
            "system_prompt": self.system_prompt,
            "model": self.model_settings,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "capabilities": self.capabilities,
            "skill_ids": [str(skill_id) for skill_id in self.skill_ids],
            "tool_ids": [str(tool_id) for tool_id in self.tool_ids],
            "knowledge_base_ids": [str(knowledge_id) for knowledge_id in self.knowledge_base_ids],
            "approval_policy": self.approval_policy,
            "release_strategy": self.release_strategy,
        }


def is_runnable_employee_definition(definition: Mapping[str, object]) -> bool:
    capabilities = definition.get("capabilities")
    return (
        definition.get("work_mode") == RuntimeType.AUTONOMOUS.value
        and isinstance(capabilities, Mapping)
        and isinstance(capabilities.get("conversation"), bool)
        and capabilities.get("scheduled_tasks") is False
        and capabilities.get("file_upload") is False
    )


@dataclass(frozen=True, slots=True)
class Employee:
    id: UUID
    tenant_id: UUID
    created_by: UUID
    draft: EmployeeDraft
    status: EmployeeStatus
    published_version: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        created_by: UUID,
        draft: EmployeeDraft,
    ) -> "Employee":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            created_by=created_by,
            draft=draft.normalized(),
            status=EmployeeStatus.DRAFT,
            published_version=None,
            created_at=now,
            updated_at=now,
        )

    def update(self, draft: EmployeeDraft) -> "Employee":
        return replace(
            self,
            draft=draft.normalized(),
            status=EmployeeStatus.DRAFT,
            updated_at=datetime.now(UTC),
        )

    def publish(self, *, published_by: UUID) -> tuple["Employee", "EmployeeVersion"]:
        version_number = (self.published_version or 0) + 1
        published_at = datetime.now(UTC)
        return (
            replace(
                self,
                status=EmployeeStatus.PUBLISHED,
                published_version=version_number,
                updated_at=published_at,
            ),
            EmployeeVersion(
                id=uuid4(),
                employee_id=self.id,
                tenant_id=self.tenant_id,
                version=version_number,
                definition=self.draft.snapshot(),
                published_by=published_by,
                published_at=published_at,
            ),
        )


@dataclass(frozen=True, slots=True)
class EmployeeVersion:
    id: UUID
    employee_id: UUID
    tenant_id: UUID
    version: int
    definition: dict[str, object]
    published_by: UUID
    published_at: datetime
