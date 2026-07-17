from __future__ import annotations

from uuid import UUID

from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeVersion,
    RuntimeType,
    is_runnable_employee_definition,
)
from agent_platform.platform.employees.errors import (
    EmployeeConfigurationUnavailable,
    EmployeeKnowledgeBaseNotBindable,
    EmployeeNotFound,
    EmployeeSkillNotBindable,
    EmployeeToolNotBindable,
    EmployeeWorkflowNotBindable,
)
from agent_platform.platform.employees.ports import (
    EmployeeKnowledgeBasePolicy,
    EmployeeRepository,
    EmployeeSkillPolicy,
    EmployeeToolPolicy,
    EmployeeVersionRepository,
    EmployeeWorkflowPolicy,
)

_WORKFLOW_RUNTIME_TYPES = {RuntimeType.WORKFLOW, RuntimeType.HYBRID}


class EmployeeService:
    def __init__(
        self,
        *,
        employees: EmployeeRepository,
        versions: EmployeeVersionRepository,
        skills: EmployeeSkillPolicy,
        tools: EmployeeToolPolicy,
        knowledge_bases: EmployeeKnowledgeBasePolicy,
        workflows: EmployeeWorkflowPolicy,
    ) -> None:
        self._employees = employees
        self._versions = versions
        self._skills = skills
        self._tools = tools
        self._knowledge_bases = knowledge_bases
        self._workflows = workflows

    async def create(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        draft: EmployeeDraft,
    ) -> Employee:
        await self._ensure_skills_bindable(tenant_id=tenant_id, skill_ids=draft.skill_ids)
        await self._ensure_tools_bindable(tenant_id=tenant_id, tool_ids=draft.tool_ids)
        await self._ensure_knowledge_bases_bindable(
            tenant_id=tenant_id,
            knowledge_base_ids=draft.knowledge_base_ids,
        )
        await self._ensure_workflow_bindable(tenant_id=tenant_id, draft=draft)
        employee = Employee.create(tenant_id=tenant_id, created_by=created_by, draft=draft)
        await self._employees.add(employee)
        return employee

    async def update(
        self,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        draft: EmployeeDraft,
    ) -> Employee:
        await self._ensure_skills_bindable(tenant_id=tenant_id, skill_ids=draft.skill_ids)
        await self._ensure_tools_bindable(tenant_id=tenant_id, tool_ids=draft.tool_ids)
        await self._ensure_knowledge_bases_bindable(
            tenant_id=tenant_id,
            knowledge_base_ids=draft.knowledge_base_ids,
        )
        await self._ensure_workflow_bindable(tenant_id=tenant_id, draft=draft)
        employee = await self._required_employee(tenant_id=tenant_id, employee_id=employee_id)
        updated = employee.update(draft)
        await self._employees.update(updated)
        return updated

    async def publish(
        self,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        published_by: UUID,
    ) -> Employee:
        employee = await self._required_employee(tenant_id=tenant_id, employee_id=employee_id)
        workflow_version = await self._fixate_workflow_version(
            tenant_id=tenant_id,
            draft=employee.draft,
        )
        if not is_runnable_employee_definition(
            employee.draft.snapshot(workflow_version=workflow_version)
        ):
            raise EmployeeConfigurationUnavailable
        await self._ensure_skills_bindable(
            tenant_id=tenant_id,
            skill_ids=employee.draft.skill_ids,
        )
        skill_versions = await self._skills.published_versions(
            tenant_id=tenant_id,
            skill_ids=employee.draft.skill_ids,
        )
        await self._ensure_tools_bindable(
            tenant_id=tenant_id,
            tool_ids=employee.draft.tool_ids,
        )
        await self._ensure_knowledge_bases_bindable(
            tenant_id=tenant_id,
            knowledge_base_ids=employee.draft.knowledge_base_ids,
        )
        published, version = employee.publish(
            published_by=published_by,
            skill_versions=skill_versions,
            workflow_version=workflow_version,
        )
        await self._employees.update(published)
        await self._versions.add(version)
        return published

    async def get(self, *, tenant_id: UUID, employee_id: UUID) -> Employee:
        return await self._required_employee(tenant_id=tenant_id, employee_id=employee_id)

    async def list_all(self, *, tenant_id: UUID) -> list[Employee]:
        return await self._employees.list(tenant_id=tenant_id)

    async def list_versions(
        self,
        *,
        tenant_id: UUID,
        employee_id: UUID,
    ) -> list[EmployeeVersion]:
        await self._required_employee(tenant_id=tenant_id, employee_id=employee_id)
        return await self._versions.list(tenant_id=tenant_id, employee_id=employee_id)

    async def _required_employee(self, *, tenant_id: UUID, employee_id: UUID) -> Employee:
        employee = await self._employees.get(tenant_id=tenant_id, employee_id=employee_id)
        if employee is None:
            raise EmployeeNotFound
        return employee

    async def _ensure_skills_bindable(
        self,
        *,
        tenant_id: UUID,
        skill_ids: list[UUID],
    ) -> None:
        if not await self._skills.are_bindable(tenant_id=tenant_id, skill_ids=skill_ids):
            raise EmployeeSkillNotBindable

    async def _ensure_tools_bindable(
        self,
        *,
        tenant_id: UUID,
        tool_ids: list[UUID],
    ) -> None:
        if not await self._tools.are_bindable(tenant_id=tenant_id, tool_ids=tool_ids):
            raise EmployeeToolNotBindable

    async def _ensure_knowledge_bases_bindable(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_ids: list[UUID],
    ) -> None:
        if not await self._knowledge_bases.are_bindable(
            tenant_id=tenant_id,
            knowledge_base_ids=knowledge_base_ids,
        ):
            raise EmployeeKnowledgeBaseNotBindable

    async def _ensure_workflow_bindable(self, *, tenant_id: UUID, draft: EmployeeDraft) -> None:
        """流程/混合员工必须引用一个已注册工作流；引用未注册流程受控拒绝。"""

        if draft.runtime_type not in _WORKFLOW_RUNTIME_TYPES:
            return
        if draft.workflow_id is None:
            raise EmployeeWorkflowNotBindable
        if not await self._workflows.is_registered(
            tenant_id=tenant_id,
            workflow_id=draft.workflow_id,
        ):
            raise EmployeeWorkflowNotBindable

    async def _fixate_workflow_version(
        self,
        *,
        tenant_id: UUID,
        draft: EmployeeDraft,
    ) -> int | None:
        """发布时固化被引用工作流的当前已发布版本；未发布则拒绝发布。"""

        if draft.runtime_type not in _WORKFLOW_RUNTIME_TYPES:
            return None
        if draft.workflow_id is None:
            raise EmployeeConfigurationUnavailable
        version = await self._workflows.published_version(
            tenant_id=tenant_id,
            workflow_id=draft.workflow_id,
        )
        if version is None:
            raise EmployeeConfigurationUnavailable
        return version
