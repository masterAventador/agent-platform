import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, JsonValue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.api.routes.approvals import _map_service_error as _map_approval_error
from agent_platform.infrastructure.database.repositories.approvals import (
    create_approval_service,
)
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyFileRepository,
    SqlAlchemyTaskAttachmentRepository,
)
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunEventRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.approvals.errors import (
    ApprovalConcurrencyConflict,
    ApprovalExpired,
    ApprovalNotPending,
    ApprovalPermissionDenied,
    ApprovalReasonRequired,
    ApprovalRunNotActionable,
)
from agent_platform.platform.approvals.service import (
    DecisionAction as ApprovalDecisionAction,
)
from agent_platform.platform.artifacts.entities import TaskAttachment
from agent_platform.platform.dynamic_io import (
    DynamicInputTooLarge,
    DynamicInputValidationFailed,
    InvalidDynamicSchema,
    file_field_names,
    validate_run_input,
)
from agent_platform.platform.employees.entities import (
    EmployeeVisibility,
    is_runnable_employee_definition,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission

router = APIRouter(prefix="/api/v1", tags=["runs"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
IdempotencyHeader = Annotated[UUID | None, Header(alias="Idempotency-Key")]
StreamTenantQuery = Annotated[UUID | None, Query(alias="tenant_id")]


class CreateRunRequest(BaseModel):
    input: dict[str, JsonValue]
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ControlRunRequest(BaseModel):
    action: Literal["resume", "cancel", "approve", "reject"]
    approval_id: UUID | None = None
    reason: str | None = None


class RunResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    employee_version: int
    created_by: UUID
    thread_id: str
    input: dict[str, JsonValue]
    status: str
    error_code: str | None
    error_message: str | None
    conversation_id: UUID | None
    output_schema: dict[str, JsonValue] | None = None

    @classmethod
    def from_entity(
        cls,
        run: Run,
        *,
        output_schema: dict[str, JsonValue] | None = None,
    ) -> "RunResponse":
        return cls(
            id=run.id,
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            employee_version=run.employee_version,
            created_by=run.created_by,
            thread_id=run.thread_id,
            input=run.input_data,
            status=run.status.value,
            error_code=run.error_code,
            error_message=run.error_message,
            conversation_id=run.conversation_id,
            output_schema=output_schema,
        )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _permission_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "permission_denied", "message": "没有执行此操作的权限"},
    )


def _dynamic_input_error(error: Exception) -> HTTPException:
    if isinstance(error, DynamicInputTooLarge):
        return HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "run_input_too_large",
                "message": "任务输入超过大小限制",
            },
        )
    if isinstance(error, DynamicInputValidationFailed):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "run_input_schema_validation_failed",
                "message": "任务输入不符合数字员工发布版本的输入 Schema",
                "errors": list(error.errors),
            },
        )
    if isinstance(error, InvalidDynamicSchema):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "employee_configuration_unavailable",
                "message": "数字员工发布版本的输入 Schema 无效",
            },
        )
    raise error


def _output_schema_from_definition(
    definition: dict[str, object],
) -> dict[str, JsonValue] | None:
    output_schema = definition.get("output_schema")
    if not isinstance(output_schema, dict):
        return None
    return cast(dict[str, JsonValue], output_schema)


async def _output_schemas_for_runs(
    *,
    database_session: AsyncSession,
    runs: list[Run],
) -> dict[tuple[UUID, int], dict[str, JsonValue] | None]:
    version_repository = SqlAlchemyEmployeeVersionRepository(database_session)
    schemas: dict[tuple[UUID, int], dict[str, JsonValue] | None] = {}
    for run in runs:
        key = (run.employee_id, run.employee_version)
        if key in schemas:
            continue
        version = await version_repository.get(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            version=run.employee_version,
        )
        schemas[key] = (
            _output_schema_from_definition(version.definition) if version is not None else None
        )
    return schemas


async def _output_schema_for_run(
    database_session: AsyncSession,
    *,
    run: Run,
) -> dict[str, JsonValue] | None:
    version = await SqlAlchemyEmployeeVersionRepository(database_session).get(
        tenant_id=run.tenant_id,
        employee_id=run.employee_id,
        version=run.employee_version,
    )
    if version is None:
        return None
    return _output_schema_from_definition(version.definition)


def _deduplicated_attachment_ids(attachment_ids: list[UUID]) -> list[UUID]:
    return list(dict.fromkeys(attachment_ids))


def _ensure_dynamic_file_attachments_match_input(
    *,
    input_schema: dict[str, object],
    input_data: dict[str, JsonValue],
    attachment_ids: list[UUID],
) -> None:
    file_fields = file_field_names(input_schema)
    if not file_fields:
        return

    referenced_file_ids: list[UUID] = []
    for field_name in file_fields:
        value = input_data.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise DynamicInputValidationFailed(
                (f"{field_name}: 动态文件字段必须引用本次上传附件",)
            )
        try:
            referenced_file_ids.append(UUID(value))
        except ValueError as error:
            raise DynamicInputValidationFailed(
                (f"{field_name}: 动态文件字段必须引用本次上传附件",)
            ) from error

    referenced = set(referenced_file_ids)
    attached = set(_deduplicated_attachment_ids(attachment_ids))
    if referenced != attached:
        raise DynamicInputValidationFailed(
            ("动态文件字段必须与本次上传附件一一绑定",)
        )


async def _ensure_same_idempotent_request(
    *,
    run: Run,
    payload: CreateRunRequest,
    attachment_repository: SqlAlchemyTaskAttachmentRepository,
) -> None:
    existing_attachments = await attachment_repository.list_for_run(
        tenant_id=run.tenant_id,
        run_id=run.id,
    )
    if (
        run.input_data != payload.input
        or [item.file_id for item in existing_attachments]
        != _deduplicated_attachment_ids(payload.attachment_ids)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_key_reused",
                "message": "幂等键已用于不同的任务请求",
            },
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
    idempotency_key: IdempotencyHeader = None,
) -> RunResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        employee = await SqlAlchemyEmployeeRepository(database_session).get(
            tenant_id=access.tenant.id,
            employee_id=employee_id,
        )
        if employee is None:
            raise _not_found()
        can_manage_employees = role_has_permission(
            role=access.role,
            permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        if employee.published_version is None:
            if not can_manage_employees:
                raise _not_found()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "employee_not_published", "message": "数字员工尚未发布"},
            )

        runs = SqlAlchemyRunRepository(database_session)
        attachment_repository = SqlAlchemyTaskAttachmentRepository(database_session)
        if idempotency_key is not None:
            existing = await runs.get_by_idempotency_key(
                tenant_id=access.tenant.id,
                created_by=user.id,
                employee_id=employee.id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                await _ensure_same_idempotent_request(
                    run=existing,
                    payload=payload,
                    attachment_repository=attachment_repository,
                )
                return RunResponse.from_entity(
                    existing,
                    output_schema=await _output_schema_for_run(
                        database_session,
                        run=existing,
                    ),
                )

        version = await SqlAlchemyEmployeeVersionRepository(database_session).get(
            tenant_id=access.tenant.id,
            employee_id=employee.id,
            version=employee.published_version,
        )
        if version is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "employee_configuration_unavailable",
                    "message": "数字员工配置当前不可运行",
                },
            )
        if (
            not can_manage_employees
            and version.definition.get("visibility") != EmployeeVisibility.TENANT.value
        ):
            raise _not_found()
        if not is_runnable_employee_definition(version.definition):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "employee_configuration_unavailable",
                    "message": "数字员工配置当前不可运行",
                },
            )
        input_schema = version.definition.get("input_schema")
        if not isinstance(input_schema, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "employee_configuration_unavailable",
                    "message": "数字员工发布版本缺少输入 Schema",
                },
            )
        capabilities = version.definition.get("capabilities")
        file_upload_enabled = (
            capabilities.get("file_upload") is True if isinstance(capabilities, dict) else False
        )
        try:
            validate_run_input(
                input_schema=input_schema,
                value=payload.input,
                file_upload_enabled=file_upload_enabled,
            )
            _ensure_dynamic_file_attachments_match_input(
                input_schema=input_schema,
                input_data=payload.input,
                attachment_ids=payload.attachment_ids,
            )
        except (DynamicInputTooLarge, DynamicInputValidationFailed, InvalidDynamicSchema) as error:
            raise _dynamic_input_error(error) from error

        if payload.attachment_ids and (
            not file_upload_enabled
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "file_upload_disabled", "message": "数字员工未启用文件输入"},
            )
        file_repository = SqlAlchemyFileRepository(database_session)
        attachment_repository = SqlAlchemyTaskAttachmentRepository(database_session)
        attachment_files = []
        for file_id in dict.fromkeys(payload.attachment_ids):
            file = await file_repository.get(tenant_id=access.tenant.id, file_id=file_id)
            if file is None or (
                file.owner_id != user.id
                and not role_has_permission(
                    role=access.role, permission=TenantPermission.RUNS_MANAGE
                )
            ):
                raise _not_found()
            attachment_files.append(file)

        run = Run.create(
            tenant_id=access.tenant.id,
            employee_id=employee.id,
            employee_version=employee.published_version,
            created_by=user.id,
            input_data=payload.input,
            idempotency_key=idempotency_key,
        )
        await runs.add(run)
        for file in attachment_files:
            await attachment_repository.add(
                TaskAttachment.create(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    file_id=file.id,
                    workspace_path=f"inputs/{file.id}/{file.name}",
                )
            )
        await SqlAlchemyRunCommandRepository(database_session).add(
            RunCommand.create(
                run_id=run.id,
                tenant_id=run.tenant_id,
                action=RunCommandAction.START,
            )
        )
        await emit_audit_event(
            database_session,
            tenant_id=run.tenant_id,
            actor_user_id=user.id,
            action="run.created",
            resource_type="run",
            resource_id=run.id,
            metadata={
                "employee_id": str(run.employee_id),
                "employee_version": run.employee_version,
                "attachment_count": len(attachment_files),
            },
        )
        try:
            await database_session.commit()
        except IntegrityError:
            await database_session.rollback()
            if idempotency_key is None:
                raise
            existing = await runs.get_by_idempotency_key(
                tenant_id=access.tenant.id,
                created_by=user.id,
                employee_id=employee.id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            await _ensure_same_idempotent_request(
                run=existing,
                payload=payload,
                attachment_repository=attachment_repository,
            )
            run = existing
        output_schema = await _output_schema_for_run(database_session, run=run)
    return RunResponse.from_entity(
        run,
        output_schema=output_schema,
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> RunResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        run = await SqlAlchemyRunRepository(database_session).get(
            tenant_id=access.tenant.id,
            run_id=run_id,
        )
        if run is None:
            raise _not_found()
        if (
            not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
            and run.created_by != user.id
        ):
            raise _not_found()
        version = await SqlAlchemyEmployeeVersionRepository(database_session).get(
            tenant_id=run.tenant_id,
            employee_id=run.employee_id,
            version=run.employee_version,
        )
    return RunResponse.from_entity(
        run,
        output_schema=(
            _output_schema_from_definition(version.definition) if version is not None else None
        ),
    )


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[RunResponse]:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        runs = await SqlAlchemyRunRepository(database_session).list(
            tenant_id=access.tenant.id,
            limit=limit,
            created_by=(
                None
                if role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
                else user.id
            ),
        )
        output_schemas = await _output_schemas_for_runs(
            database_session=database_session,
            runs=runs,
        )
    return [
        RunResponse.from_entity(
            run,
            output_schema=output_schemas.get((run.employee_id, run.employee_version)),
        )
        for run in runs
    ]


@router.get("/runs/{run_id}/events", response_model=list[PlatformEvent])
async def list_run_events(
    run_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
) -> list[PlatformEvent]:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        run = await SqlAlchemyRunRepository(database_session).get(
            tenant_id=access.tenant.id,
            run_id=run_id,
        )
        if run is None:
            raise _not_found()
        if (
            not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
            and run.created_by != user.id
        ):
            raise _not_found()
        return await SqlAlchemyRunEventRepository(database_session).list(
            run_id=run_id,
            after_sequence=after_sequence,
        )


@router.post(
    "/runs/{run_id}/control",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
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
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        runs = SqlAlchemyRunRepository(database_session)
        run = await runs.get_for_update(tenant_id=access.tenant.id, run_id=run_id)
        if run is None:
            raise _not_found()
        can_manage_runs = role_has_permission(
            role=access.role, permission=TenantPermission.RUNS_MANAGE
        )
        if not can_manage_runs and run.created_by != user.id:
            raise _not_found()
        if payload.action in {"approve", "reject"} and not can_manage_runs:
            raise _permission_denied()

        updated = _apply_control_request(run, payload)

        if payload.action in {"approve", "reject"} and payload.approval_id is not None:
            # C13：run 控制入口与审批中心共用同一审批协议，先结算审批记录，
            # 由 ApprovalService 统一创建 run 命令/事件/审计，不留旁路通道。
            settled = await _settle_approval_record(
                database_session=database_session,
                run=run,
                payload=payload,
                actor_id=user.id,
                actor_role=access.role,
            )
            if settled:
                await emit_audit_event(
                    database_session,
                    tenant_id=run.tenant_id,
                    actor_user_id=user.id,
                    action="run.control_requested",
                    resource_type="run",
                    resource_id=run.id,
                    metadata={
                        "requested_action": payload.action,
                        "reason_present": payload.reason is not None,
                    },
                )
                await database_session.commit()
                return RunResponse.from_entity(updated)
            # 安全 fail-closed：run 已在 WAITING_FOR_APPROVAL（_apply_control_request 已校验）
            # 却查无对应审批记录，属旁路窗口，必须拒绝，绝不静默回退老通道直发 raw 命令。
            await database_session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "approval_record_missing",
                    "message": "该任务处于等待审批但缺少审批记录，请通过审批中心处理",
                },
            )

        command_payload: dict[str, JsonValue] = {"requested_by": str(user.id)}
        if payload.approval_id is not None:
            command_payload["approval_id"] = str(payload.approval_id)
        if payload.reason is not None:
            command_payload["reason"] = payload.reason
        commands = SqlAlchemyRunCommandRepository(database_session)
        if payload.action == "cancel" and (
            await commands.unprocessed_cancel_commands(run_id=run.id)
        ):
            await database_session.commit()
            return RunResponse.from_entity(updated)
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
                event_type=EventType.RUN_PROGRESS,
                payload={
                    "action": (
                        f"{payload.action}_requested"
                        if payload.action in {"cancel", "reject"}
                        else payload.action
                    ),
                    **command_payload,
                },
            )
        )
        await emit_audit_event(
            database_session,
            tenant_id=run.tenant_id,
            actor_user_id=user.id,
            action="run.control_requested",
            resource_type="run",
            resource_id=run.id,
            metadata={
                "requested_action": payload.action,
                "reason_present": payload.reason is not None,
            },
        )
        await database_session.commit()
    return RunResponse.from_entity(updated)


async def _settle_approval_record(
    *,
    database_session: AsyncSession,
    run: Run,
    payload: ControlRunRequest,
    actor_id: UUID,
    actor_role: TenantRole,
) -> bool:
    """结算 run + invocation 对应的审批记录；返回是否存在并已处理。

    没有审批记录（历史数据/测试直造）时返回 False，调用方走原有流程。
    """

    assert payload.approval_id is not None
    try:
        settled = await create_approval_service(database_session).decide_by_invocation(
            tenant_id=run.tenant_id,
            run_id=run.id,
            invocation_id=payload.approval_id,
            action=cast(ApprovalDecisionAction, payload.action),
            actor_id=actor_id,
            actor_role=actor_role,
            reason=payload.reason,
        )
    except ApprovalExpired as error:
        await database_session.commit()  # 惰性过期结算需要落库
        raise _map_approval_error(error) from error
    except (
        ApprovalNotPending,
        ApprovalConcurrencyConflict,
        ApprovalPermissionDenied,
        ApprovalReasonRequired,
        ApprovalRunNotActionable,
    ) as error:
        await database_session.rollback()
        raise _map_approval_error(error) from error
    return settled is not None


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
    if payload.action == "cancel" and run.status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_run_transition",
                "message": f"{run.status.value} 不接受 cancel",
            },
        )
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
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=selected_tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
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
        if (
            not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
            and run.created_by != user.id
        ):
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
