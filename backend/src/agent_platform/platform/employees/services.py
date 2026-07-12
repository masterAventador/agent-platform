from __future__ import annotations

from uuid import UUID

from agent_platform.platform.employees.entities import Employee, EmployeeDraft, EmployeeVersion
from agent_platform.platform.employees.errors import EmployeeNotFound, EmployeeSkillNotBindable
from agent_platform.platform.employees.ports import (
    EmployeeRepository,
    EmployeeSkillPolicy,
    EmployeeVersionRepository,
)


class EmployeeService:
    def __init__(
        self,
        *,
        employees: EmployeeRepository,
        versions: EmployeeVersionRepository,
        skills: EmployeeSkillPolicy,
    ) -> None:
        self._employees = employees
        self._versions = versions
        self._skills = skills

    async def create(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        draft: EmployeeDraft,
    ) -> Employee:
        await self._ensure_skills_bindable(tenant_id=tenant_id, skill_ids=draft.skill_ids)
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
        await self._ensure_skills_bindable(
            tenant_id=tenant_id,
            skill_ids=employee.draft.skill_ids,
        )
        published, version = employee.publish(published_by=published_by)
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
