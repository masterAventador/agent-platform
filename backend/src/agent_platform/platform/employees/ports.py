from typing import Protocol
from uuid import UUID

from agent_platform.platform.employees.entities import Employee, EmployeeVersion


class EmployeeRepository(Protocol):
    async def add(self, employee: Employee) -> None: ...

    async def update(self, employee: Employee) -> None: ...

    async def get(self, *, tenant_id: UUID, employee_id: UUID) -> Employee | None: ...

    async def list(self, *, tenant_id: UUID) -> list[Employee]: ...


class EmployeeVersionRepository(Protocol):
    async def add(self, version: EmployeeVersion) -> None: ...

    async def list(self, *, tenant_id: UUID, employee_id: UUID) -> list[EmployeeVersion]: ...


class EmployeeSkillPolicy(Protocol):
    async def are_bindable(self, *, tenant_id: UUID, skill_ids: list[UUID]) -> bool: ...
