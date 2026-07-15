from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.workbench import (
    SqlAlchemyWorkbenchSummaryReader,
)
from agent_platform.platform.tenants.permissions import TenantPermission
from agent_platform.platform.workbench.models import WorkbenchSummary
from agent_platform.platform.workbench.services import WorkbenchService

router = APIRouter(prefix="/api/v1/workbench", tags=["workbench"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
NonNegativeInt = Annotated[int, Field(ge=0)]


class EmployeeCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    total: NonNegativeInt
    draft: NonNegativeInt
    published: NonNegativeInt


class RunCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    total: NonNegativeInt
    queued: NonNegativeInt
    running: NonNegativeInt
    waiting_for_input: NonNegativeInt
    waiting_for_approval: NonNegativeInt
    completed: NonNegativeInt
    failed: NonNegativeInt
    cancelled: NonNegativeInt


class WorkbenchSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    employees: EmployeeCountsResponse
    runs: RunCountsResponse

    @classmethod
    def from_summary(cls, summary: WorkbenchSummary) -> "WorkbenchSummaryResponse":
        return cls(
            employees=EmployeeCountsResponse(
                total=summary.employees.total,
                draft=summary.employees.draft,
                published=summary.employees.published,
            ),
            runs=RunCountsResponse(
                total=summary.runs.total,
                queued=summary.runs.queued,
                running=summary.runs.running,
                waiting_for_input=summary.runs.waiting_for_input,
                waiting_for_approval=summary.runs.waiting_for_approval,
                completed=summary.runs.completed,
                failed=summary.runs.failed,
                cancelled=summary.runs.cancelled,
            ),
        )


@router.get("/summary", response_model=WorkbenchSummaryResponse)
async def get_workbench_summary(
    request: Request,
    tenant_id: TenantHeader = None,
) -> WorkbenchSummaryResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        summary = await WorkbenchService(
            SqlAlchemyWorkbenchSummaryReader(database_session)
        ).get_summary(
            tenant_id=access.tenant.id,
            user_id=user.id,
            role=access.role,
        )
    return WorkbenchSummaryResponse.from_summary(summary)
