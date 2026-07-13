import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, JsonValue

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.employees.entities import (
    EmployeeStatus,
    is_runnable_employee_definition,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.errors import InvalidRunTransition
from agent_platform.platform.runs.events import EventType, PlatformEvent

router = APIRouter(prefix="/api/v1", tags=["runs"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
StreamTenantQuery = Annotated[UUID | None, Query(alias="tenant_id")]


class CreateRunRequest(BaseModel):
    input: dict[str, JsonValue]


class ControlRunRequest(BaseModel):
    action: Literal["resume", "cancel", "approve", "reject"]
    approval_id: UUID | None = None
    reason: str | None = None


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

        version = await SqlAlchemyEmployeeVersionRepository(database_session).get(
            tenant_id=access.tenant.id,
            employee_id=employee.id,
            version=employee.published_version,
        )
        if version is None or not is_runnable_employee_definition(version.definition):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "employee_configuration_unavailable",
                    "message": "数字员工配置当前不可运行",
                },
            )

        run = Run.create(
            tenant_id=access.tenant.id,
            employee_id=employee.id,
            employee_version=employee.published_version,
            created_by=user.id,
            input_data=payload.input,
        )
        await SqlAlchemyRunRepository(database_session).add(run)
        await SqlAlchemyRunCommandRepository(database_session).add(
            RunCommand.create(
                run_id=run.id,
                tenant_id=run.tenant_id,
                action=RunCommandAction.START,
            )
        )
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


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[RunResponse]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        runs = await SqlAlchemyRunRepository(database_session).list(
            tenant_id=access.tenant.id, limit=limit
        )
    return [RunResponse.from_entity(run) for run in runs]


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


@router.post("/runs/{run_id}/control", response_model=RunResponse)
async def control_run(
    run_id: UUID,
    payload: ControlRunRequest,
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
        runs = SqlAlchemyRunRepository(database_session)
        run = await runs.get_for_update(tenant_id=access.tenant.id, run_id=run_id)
        if run is None:
            raise _not_found()

        updated = _apply_control_request(run, payload)

        command_payload: dict[str, JsonValue] = {"requested_by": str(user.id)}
        if payload.approval_id is not None:
            command_payload["approval_id"] = str(payload.approval_id)
        if payload.reason is not None:
            command_payload["reason"] = payload.reason
        commands = SqlAlchemyRunCommandRepository(database_session)
        await runs.update(updated)
        await commands.add(
            RunCommand.create(
                run_id=run.id,
                tenant_id=run.tenant_id,
                action=RunCommandAction(payload.action),
                payload=command_payload,
            )
        )
        events = SqlAlchemyRunEventRepository(database_session)
        await events.append(
            PlatformEvent.create(
                tenant_id=run.tenant_id,
                employee_id=run.employee_id,
                run_id=run.id,
                sequence=await events.next_sequence(run_id=run.id),
                event_type=(
                    EventType.RUN_CANCELLED
                    if payload.action in {"cancel", "reject"}
                    else EventType.RUN_PROGRESS
                ),
                payload={"action": payload.action, **command_payload},
            )
        )
        await database_session.commit()
    return RunResponse.from_entity(updated)


def _apply_control_request(run: Run, payload: ControlRunRequest) -> Run:
    if payload.action in {"approve", "reject"}:
        if payload.approval_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "approval_id_required", "message": "缺少审批 ID"},
            )
        required_status = RunStatus.WAITING_FOR_APPROVAL
    elif payload.action == "resume":
        required_status = RunStatus.WAITING_FOR_INPUT
    else:
        required_status = None

    if required_status is not None and run.status is not required_status:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_run_transition",
                "message": f"{run.status.value} 不接受 {payload.action}",
            },
        )
    if payload.action in {"cancel", "reject"}:
        try:
            return run.transition_to(RunStatus.CANCELLED)
        except InvalidRunTransition as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invalid_run_transition", "message": str(error)},
            ) from error
    return run


@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    tenant_header: TenantHeader = None,
    stream_tenant_id: StreamTenantQuery = None,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    selected_tenant_id = stream_tenant_id or tenant_header
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=selected_tenant_id,
            owner_required=False,
        )
        if (
            tenant_header is not None
            and stream_tenant_id is not None
            and tenant_header != stream_tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "tenant_context_conflict",
                    "message": "租户请求上下文不一致",
                },
            )
        run = await SqlAlchemyRunRepository(database_session).get(
            tenant_id=access.tenant.id, run_id=run_id
        )
        if run is None:
            raise _not_found()

    async def generate() -> AsyncIterator[str]:
        cursor = after_sequence
        while not await request.is_disconnected():
            async with request.app.state.session_factory() as session:
                run_repository = SqlAlchemyRunRepository(session)
                current = await run_repository.get(tenant_id=access.tenant.id, run_id=run_id)
                events = await SqlAlchemyRunEventRepository(session).list(
                    run_id=run_id, after_sequence=cursor
                )
            for event in events:
                cursor = event.sequence
                data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n"
            if current is None or current.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
