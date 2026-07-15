from typing import Protocol
from uuid import UUID

from agent_platform.platform.workbench.models import WorkbenchSummary


class WorkbenchSummaryReader(Protocol):
    async def read(
        self,
        *,
        tenant_id: UUID,
        include_draft_and_private_employees: bool,
        run_created_by: UUID | None,
    ) -> WorkbenchSummary: ...
