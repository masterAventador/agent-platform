from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.infrastructure.database.repositories.employees import EmployeeRecord
from agent_platform.infrastructure.database.repositories.runs import RunRecord
from agent_platform.platform.employees.entities import EmployeeStatus, EmployeeVisibility
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.workbench.models import EmployeeCounts, RunCounts, WorkbenchSummary


def _map_run_status_counts(values: Sequence[int | None]) -> dict[RunStatus, int]:
    return {
        status: int(value or 0)
        for status, value in zip(RunStatus, values, strict=True)
    }


class SqlAlchemyWorkbenchSummaryReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(
        self,
        *,
        tenant_id: UUID,
        include_draft_and_private_employees: bool,
        run_created_by: UUID | None,
    ) -> WorkbenchSummary:
        employee_query = select(
            func.count(EmployeeRecord.id),
            func.sum(case((EmployeeRecord.status == EmployeeStatus.DRAFT.value, 1), else_=0)),
            func.sum(
                case((EmployeeRecord.status == EmployeeStatus.PUBLISHED.value, 1), else_=0)
            ),
        ).where(EmployeeRecord.tenant_id == tenant_id)
        if not include_draft_and_private_employees:
            employee_query = employee_query.where(
                EmployeeRecord.status == EmployeeStatus.PUBLISHED.value,
                EmployeeRecord.visibility == EmployeeVisibility.TENANT.value,
            )
        employee_row = (await self._session.execute(employee_query)).one()

        status_columns = tuple(
            func.sum(case((RunRecord.status == status.value, 1), else_=0))
            for status in RunStatus
        )
        run_query = select(func.count(RunRecord.id), *status_columns).where(
            RunRecord.tenant_id == tenant_id
        )
        if run_created_by is not None:
            run_query = run_query.where(RunRecord.created_by == run_created_by)
        run_row = (await self._session.execute(run_query)).one()
        status_counts = _map_run_status_counts(run_row[1:])

        return WorkbenchSummary(
            employees=EmployeeCounts(
                total=int(employee_row[0] or 0),
                draft=int(employee_row[1] or 0),
                published=int(employee_row[2] or 0),
            ),
            runs=RunCounts(
                total=int(run_row[0] or 0),
                queued=status_counts[RunStatus.QUEUED],
                running=status_counts[RunStatus.RUNNING],
                waiting_for_input=status_counts[RunStatus.WAITING_FOR_INPUT],
                waiting_for_approval=status_counts[RunStatus.WAITING_FOR_APPROVAL],
                completed=status_counts[RunStatus.COMPLETED],
                failed=status_counts[RunStatus.FAILED],
                cancelled=status_counts[RunStatus.CANCELLED],
            ),
        )
