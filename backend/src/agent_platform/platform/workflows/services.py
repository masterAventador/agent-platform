from __future__ import annotations

from uuid import UUID

from agent_platform.platform.workflows.entities import (
    Workflow,
    WorkflowVersion,
)
from agent_platform.platform.workflows.errors import (
    WorkflowNotFound,
    WorkflowNotPublished,
    WorkflowVersionNotFound,
)
from agent_platform.platform.workflows.ports import (
    WorkflowRepository,
    WorkflowVersionRepository,
)


class WorkflowService:
    """工作流注册、版本、发布与回滚。

    与 Skill/Tool 版本化同风格：``published_version`` 指向当前对外可引用的版本，
    员工发布时固化该版本号；后续 ``rollback``/``add_version`` 只改注册表的当前指针，
    不改动任何已存在版本的图快照，因此不影响已固化引用的运行语义。
    """

    def __init__(
        self,
        *,
        workflows: WorkflowRepository,
        versions: WorkflowVersionRepository,
    ) -> None:
        self._workflows = workflows
        self._versions = versions

    async def register(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        description: str,
        graph: dict[str, object],
    ) -> Workflow:
        workflow = Workflow.create(
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            description=description,
        )
        version = WorkflowVersion.create(
            workflow=workflow,
            version=1,
            graph=graph,
            description=description,
            created_by=created_by,
        )
        await self._workflows.add(workflow)
        await self._versions.add(version)
        return workflow

    async def add_version(
        self,
        *,
        tenant_id: UUID,
        workflow_id: UUID,
        created_by: UUID,
        graph: dict[str, object],
        description: str,
    ) -> Workflow:
        workflow = await self._required(tenant_id=tenant_id, workflow_id=workflow_id)
        updated = workflow.add_version(description=description)
        version = WorkflowVersion.create(
            workflow=updated,
            version=updated.latest_version,
            graph=graph,
            description=description,
            created_by=created_by,
        )
        await self._versions.add(version)
        await self._workflows.update(updated)
        return updated

    async def publish(
        self,
        *,
        tenant_id: UUID,
        workflow_id: UUID,
        version: int,
        published_by: UUID,
    ) -> Workflow:
        return await self._set_published(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            version=version,
        )

    async def rollback(
        self,
        *,
        tenant_id: UUID,
        workflow_id: UUID,
        version: int,
        rolled_back_by: UUID,
    ) -> Workflow:
        return await self._set_published(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            version=version,
        )

    async def _set_published(
        self,
        *,
        tenant_id: UUID,
        workflow_id: UUID,
        version: int,
    ) -> Workflow:
        workflow = await self._required(tenant_id=tenant_id, workflow_id=workflow_id)
        target = await self._versions.get(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            version=version,
        )
        if target is None:
            raise WorkflowVersionNotFound
        published = workflow.publish(version)
        await self._workflows.update(published)
        return published

    async def get(self, *, tenant_id: UUID, workflow_id: UUID) -> Workflow:
        return await self._required(tenant_id=tenant_id, workflow_id=workflow_id)

    async def list_all(self, *, tenant_id: UUID) -> list[Workflow]:
        return await self._workflows.list(tenant_id=tenant_id)

    async def list_versions(
        self, *, tenant_id: UUID, workflow_id: UUID
    ) -> list[WorkflowVersion]:
        await self._required(tenant_id=tenant_id, workflow_id=workflow_id)
        return await self._versions.list(tenant_id=tenant_id, workflow_id=workflow_id)

    async def get_version(
        self, *, tenant_id: UUID, workflow_id: UUID, version: int
    ) -> WorkflowVersion:
        found = await self._versions.get(
            tenant_id=tenant_id, workflow_id=workflow_id, version=version
        )
        if found is None:
            raise WorkflowVersionNotFound
        return found

    async def published_reference(self, *, tenant_id: UUID, workflow_id: UUID) -> int:
        """返回工作流当前已发布版本号；未发布则拒绝（员工引用固化时调用）。"""

        workflow = await self._required(tenant_id=tenant_id, workflow_id=workflow_id)
        if workflow.published_version is None:
            raise WorkflowNotPublished
        return workflow.published_version

    async def _required(self, *, tenant_id: UUID, workflow_id: UUID) -> Workflow:
        workflow = await self._workflows.get(tenant_id=tenant_id, workflow_id=workflow_id)
        if workflow is None:
            raise WorkflowNotFound
        return workflow
