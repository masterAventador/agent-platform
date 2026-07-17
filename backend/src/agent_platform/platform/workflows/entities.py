from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from agent_platform.platform.workflows.graph_spec import parse_workflow_graph


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


@dataclass(frozen=True, slots=True)
class Workflow:
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    latest_version: int
    published_version: int | None
    status: WorkflowStatus
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        description: str,
    ) -> Workflow:
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name.strip(),
            description=description.strip(),
            latest_version=1,
            published_version=None,
            status=WorkflowStatus.DRAFT,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def add_version(self, *, description: str) -> Workflow:
        return replace(
            self,
            description=description.strip(),
            latest_version=self.latest_version + 1,
            updated_at=datetime.now(UTC),
        )

    def publish(self, version: int) -> Workflow:
        return replace(
            self,
            published_version=version,
            status=WorkflowStatus.PUBLISHED,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class WorkflowVersion:
    id: UUID
    workflow_id: UUID
    tenant_id: UUID
    version: int
    description: str
    graph: dict[str, object]
    created_by: UUID
    created_at: datetime
    published_at: datetime | None

    @classmethod
    def create(
        cls,
        *,
        workflow: Workflow,
        version: int,
        graph: dict[str, object],
        description: str,
        created_by: UUID,
    ) -> WorkflowVersion:
        # 存图前统一做静态校验并规范化为 JSON dict，保证入库的图一定可编译。
        normalized = parse_workflow_graph(graph).to_json_dict()
        return cls(
            id=uuid4(),
            workflow_id=workflow.id,
            tenant_id=workflow.tenant_id,
            version=version,
            description=description.strip(),
            graph=dict(normalized),
            created_by=created_by,
            created_at=datetime.now(UTC),
            published_at=None,
        )
