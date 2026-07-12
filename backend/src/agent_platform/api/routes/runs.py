from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, JsonValue

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.employees.entities import EmployeeStatus
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.runs.events import PlatformEvent

router = APIRouter(prefix="/api/v1", tags=["runs"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class CreateRunRequest(BaseModel):
    input: dict[str, JsonValue]


class RunResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    employee_version: int
    thread_id: str
    input: dict[str, JsonValue]
    status: str
    error_code: str | None
    error_message: str | None

    @classmethod
    def from_entity(cls, run: Run) -> "RunResponse":
        return cls(
            id=run.id,
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            employee_version=run.employee_version,
            thread_id=run.thread_id,
            input=run.input_data,
            status=run.status.value,
            error_code=run.error_code,
            error_message=run.error_message,
        )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


@router.post(
    "/employees/{employee_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    employee_id: UUID,
    payload: CreateRunRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> RunResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        employee = await SqlAlchemyEmployeeRepository(database_session).get(
            tenant_id=access.tenant.id,
            employee_id=employee_id,
        )
        if employee is None:
            raise _not_found()
        if employee.status is not EmployeeStatus.PUBLISHED or employee.published_version is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "employee_not_published", "message": "数字员工尚未发布"},
            )

        run = Run.create(
            tenant_id=access.tenant.id,
            employee_id=employee.id,
            employee_version=employee.published_version,
            created_by=user.id,
            input_data=payload.input,
        )
        await SqlAlchemyRunRepository(database_session).add(run)
        await database_session.commit()
    return RunResponse.from_entity(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> RunResponse:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        run = await SqlAlchemyRunRepository(database_session).get(
            tenant_id=access.tenant.id,
            run_id=run_id,
        )
        if run is None:
            raise _not_found()
    return RunResponse.from_entity(run)


@router.get("/runs/{run_id}/events", response_model=list[PlatformEvent])
async def list_run_events(
    run_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> list[PlatformEvent]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        run = await SqlAlchemyRunRepository(database_session).get(
            tenant_id=access.tenant.id,
            run_id=run_id,
        )
        if run is None:
            raise _not_found()
        return await SqlAlchemyRunEventRepository(database_session).list(
            run_id=run_id,
            after_sequence=after_sequence,
        )
