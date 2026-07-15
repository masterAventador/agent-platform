from uuid import UUID

from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission
from agent_platform.platform.workbench.models import WorkbenchSummary
from agent_platform.platform.workbench.ports import WorkbenchSummaryReader


class WorkbenchService:
    def __init__(self, reader: WorkbenchSummaryReader) -> None:
        self._reader = reader

    async def get_summary(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: TenantRole,
    ) -> WorkbenchSummary:
        can_manage_employees = role_has_permission(
            role=role,
            permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        can_manage_runs = role_has_permission(
            role=role,
            permission=TenantPermission.RUNS_MANAGE,
        )
        return await self._reader.read(
            tenant_id=tenant_id,
            include_draft_and_private_employees=can_manage_employees,
            run_created_by=None if can_manage_runs else user_id,
        )
