"""定时与预约任务 API（C12）：创建、编辑、暂停/恢复、删除与执行历史。

权限沿用既有任务（run）语义：定时任务本质是「代表创建者反复发起 Run」，因此
读写统一要求 `runs.execute`；行级隔离与 runs 一致——没有 `runs.manage` 的成员
只能看到和操作自己创建的定时任务，访问他人资源一律按不存在处理。
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.params import Header
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.scheduling import (
    SqlAlchemyScheduledTaskExecutionRepository,
    SqlAlchemyScheduledTaskRepository,
)
from agent_platform.platform.dynamic_io import (
    DynamicInputTooLarge,
    DynamicInputValidationFailed,
    InvalidDynamicSchema,
    validate_run_input,
)
from agent_platform.platform.employees.entities import (
    EmployeeVisibility,
    is_runnable_employee_definition,
)
from agent_platform.platform.scheduling.entities import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    ConcurrencyPolicy,
    MisfirePolicy,
    ScheduledTask,
    ScheduledTaskExecution,
    is_scheduling_enabled,
)
from agent_platform.platform.scheduling.errors import (
    InvalidCronExpression,
    InvalidScheduledTaskTransition,
    InvalidScheduleTimezone,
    InvalidScheduleWindow,
)
from agent_platform.platform.scheduling.schedule import Schedule, ScheduleKind
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission

router = APIRouter(prefix="/api/v1/scheduled-tasks", tags=["scheduled-tasks"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class CronScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[ScheduleKind.CRON] = ScheduleKind.CRON
    cron_expression: Annotated[str, Field(min_length=1, max_length=200)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]


class OnceScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[ScheduleKind.ONCE]
    run_at: datetime
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "UTC"


ScheduleRequest = Annotated[
    CronScheduleRequest | OnceScheduleRequest, Field(discriminator="kind")
]


class ScheduledTaskWriteBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    schedule: ScheduleRequest
    input: dict[str, JsonValue] = Field(default_factory=dict)
    misfire_policy: MisfirePolicy = MisfirePolicy.SKIP
    concurrency_policy: ConcurrencyPolicy = ConcurrencyPolicy.SKIP
    max_retries: Annotated[int, Field(ge=0, le=10)] = DEFAULT_MAX_RETRIES
    retry_backoff_seconds: Annotated[int, Field(ge=1, le=86_400)] = (
        DEFAULT_RETRY_BACKOFF_SECONDS
    )


class CreateScheduledTaskRequest(ScheduledTaskWriteBase):
    employee_id: UUID


class UpdateScheduledTaskRequest(ScheduledTaskWriteBase):
    pass


class ScheduleResponse(BaseModel):
    kind: str
    timezone: str
    cron_expression: str | None
    run_at: datetime | None


class ScheduledTaskResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    created_by: UUID
    name: str
    schedule: ScheduleResponse
    input: dict[str, JsonValue]
    enabled: bool
    pause_reason: str | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    misfire_policy: str
    concurrency_policy: str
    max_retries: int
    retry_backoff_seconds: int
    revision: int
    created_at: datetime

    @classmethod
    def from_entity(cls, task: ScheduledTask) -> "ScheduledTaskResponse":
        return cls(
            id=task.id,
            tenant_id=task.tenant_id,
            employee_id=task.employee_id,
            created_by=task.created_by,
            name=task.name,
            schedule=ScheduleResponse(
                kind=task.schedule.kind.value,
                timezone=task.schedule.timezone,
                cron_expression=task.schedule.cron_expression,
                run_at=task.schedule.run_at,
            ),
            input=task.input_data,
            enabled=task.enabled,
            pause_reason=task.pause_reason.value if task.pause_reason else None,
            next_run_at=task.next_run_at,
            last_run_at=task.last_run_at,
            misfire_policy=task.misfire_policy.value,
            concurrency_policy=task.concurrency_policy.value,
            max_retries=task.max_retries,
            retry_backoff_seconds=task.retry_backoff_seconds,
            revision=task.revision,
            created_at=task.created_at,
        )


class ScheduledTaskListResponse(BaseModel):
    items: list[ScheduledTaskResponse]
    total: int
    limit: int
    offset: int


class ScheduledTaskExecutionResponse(BaseModel):
    id: UUID
    scheduled_task_id: UUID
    scheduled_for: datetime
    status: str
    attempts: int
    run_id: UUID | None
    skip_reason: str | None
    error_message: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(
        cls, execution: ScheduledTaskExecution
    ) -> "ScheduledTaskExecutionResponse":
        return cls(
            id=execution.id,
            scheduled_task_id=execution.scheduled_task_id,
            scheduled_for=execution.scheduled_for,
            status=execution.status.value,
            attempts=execution.attempts,
            run_id=execution.run_id,
            skip_reason=execution.skip_reason.value if execution.skip_reason else None,
            error_message=execution.error_message,
            next_attempt_at=execution.next_attempt_at,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
        )


class ScheduledTaskExecutionListResponse(BaseModel):
    items: list[ScheduledTaskExecutionResponse]
    total: int
    limit: int
    offset: int


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
    )


def _unprocessable(code: str, message: str, **extra: JsonValue) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message, **extra},
    )


def _build_schedule(request: CronScheduleRequest | OnceScheduleRequest) -> Schedule:
    try:
        if isinstance(request, CronScheduleRequest):
            return Schedule.cron(
                expression=request.cron_expression, timezone=request.timezone
            )
        return Schedule.once(run_at=request.run_at, timezone=request.timezone)
    except InvalidCronExpression as error:
        raise _unprocessable("invalid_cron_expression", "Cron 表达式非法") from error
    except InvalidScheduleTimezone as error:
        raise _unprocessable(
            "invalid_schedule_timezone", "时区必须是有效的 IANA 时区名"
        ) from error
    except InvalidScheduleWindow as error:
        raise _unprocessable("invalid_schedule_window", "预约时间非法") from error


async def _load_published_definition(
    *,
    database_session: AsyncSession,
    tenant_id: UUID,
    employee_id: UUID,
    can_manage_employees: bool,
) -> tuple[int, dict[str, object]]:
    """加载可调度的发布版本；不可用一律 fail-closed 报错，不静默放行。"""

    employee = await SqlAlchemyEmployeeRepository(database_session).get(
        tenant_id=tenant_id, employee_id=employee_id
    )
    if employee is None:
        raise _not_found()
    if employee.published_version is None:
        if not can_manage_employees:
            raise _not_found()
        raise _conflict("employee_not_published", "数字员工尚未发布")
    version = await SqlAlchemyEmployeeVersionRepository(database_session).get(
        tenant_id=tenant_id, employee_id=employee_id, version=employee.published_version
    )
    if version is None or not is_runnable_employee_definition(version.definition):
        raise _conflict("employee_configuration_unavailable", "数字员工配置当前不可运行")
    if (
        not can_manage_employees
        and version.definition.get("visibility") != EmployeeVisibility.TENANT.value
    ):
        raise _not_found()
    if not is_scheduling_enabled(version.definition.get("capabilities")):
        raise _conflict("scheduled_tasks_disabled", "该数字员工的发布版本未开启定时任务能力")
    return employee.published_version, version.definition


def _validate_input(definition: dict[str, object], value: dict[str, JsonValue]) -> None:
    input_schema = definition.get("input_schema")
    if not isinstance(input_schema, dict):
        raise _conflict("employee_configuration_unavailable", "数字员工发布版本缺少输入 Schema")
    capabilities = definition.get("capabilities")
    file_upload_enabled = (
        capabilities.get("file_upload") is True if isinstance(capabilities, dict) else False
    )
    try:
        validate_run_input(
            input_schema=input_schema, value=value, file_upload_enabled=file_upload_enabled
        )
    except DynamicInputTooLarge as error:
        raise _unprocessable("run_input_too_large", "任务输入超过大小限制") from error
    except DynamicInputValidationFailed as error:
        raise _unprocessable(
            "run_input_schema_validation_failed",
            "任务输入不符合数字员工发布版本的输入 Schema",
            errors=list(error.errors),
        ) from error
    except InvalidDynamicSchema as error:
        raise _conflict(
            "employee_configuration_unavailable", "数字员工发布版本的输入 Schema 无效"
        ) from error


def _ensure_no_future_occurrence(task: ScheduledTask) -> None:
    if task.next_run_at is None:
        raise _unprocessable(
            "schedule_has_no_future_occurrence", "该调度没有未来的触发时间"
        )


async def _load_own_task(
    *,
    database_session: AsyncSession,
    tenant_id: UUID,
    task_id: UUID,
    user_id: UUID,
    is_manager: bool,
) -> ScheduledTask:
    task = await SqlAlchemyScheduledTaskRepository(database_session).get(
        tenant_id=tenant_id, task_id=task_id
    )
    if task is None or (not is_manager and task.created_by != user_id):
        # 他人资源统一按不存在处理，避免探测租户内其他成员的定时任务。
        raise _not_found()
    return task


@router.post("", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_scheduled_task(
    payload: CreateScheduledTaskRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ScheduledTaskResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        published_version, definition = await _load_published_definition(
            database_session=database_session,
            tenant_id=access.tenant.id,
            employee_id=payload.employee_id,
            can_manage_employees=role_has_permission(
                role=access.role, permission=TenantPermission.EMPLOYEES_MANAGE
            ),
        )
        del published_version
        _validate_input(definition, payload.input)
        task = ScheduledTask.create(
            tenant_id=access.tenant.id,
            employee_id=payload.employee_id,
            created_by=user.id,
            name=payload.name,
            schedule=_build_schedule(payload.schedule),
            input_data=payload.input,
            now=datetime.now(UTC),
            misfire_policy=payload.misfire_policy,
            concurrency_policy=payload.concurrency_policy,
            max_retries=payload.max_retries,
            retry_backoff_seconds=payload.retry_backoff_seconds,
        )
        _ensure_no_future_occurrence(task)
        await SqlAlchemyScheduledTaskRepository(database_session).add(task)
        await emit_audit_event(
            database_session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="scheduled_task.created",
            resource_type="scheduled_task",
            resource_id=task.id,
            metadata={
                "employee_id": str(task.employee_id),
                "schedule_kind": task.schedule.kind.value,
                "timezone": task.schedule.timezone,
            },
        )
        await database_session.commit()
    return ScheduledTaskResponse.from_entity(task)


@router.get("", response_model=ScheduledTaskListResponse)
async def list_scheduled_tasks(
    request: Request,
    tenant_id: TenantHeader = None,
    employee_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScheduledTaskListResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        is_manager = role_has_permission(
            role=access.role, permission=TenantPermission.RUNS_MANAGE
        )
        items, total = await SqlAlchemyScheduledTaskRepository(database_session).list(
            tenant_id=access.tenant.id,
            created_by=None if is_manager else user.id,
            employee_id=employee_id,
            limit=limit,
            offset=offset,
        )
    return ScheduledTaskListResponse(
        items=[ScheduledTaskResponse.from_entity(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    task_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> ScheduledTaskResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        task = await _load_own_task(
            database_session=database_session,
            tenant_id=access.tenant.id,
            task_id=task_id,
            user_id=user.id,
            is_manager=role_has_permission(
                role=access.role, permission=TenantPermission.RUNS_MANAGE
            ),
        )
    return ScheduledTaskResponse.from_entity(task)


@router.patch("/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    task_id: UUID,
    payload: UpdateScheduledTaskRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ScheduledTaskResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        task = await _load_own_task(
            database_session=database_session,
            tenant_id=access.tenant.id,
            task_id=task_id,
            user_id=user.id,
            is_manager=role_has_permission(
                role=access.role, permission=TenantPermission.RUNS_MANAGE
            ),
        )
        _, definition = await _load_published_definition(
            database_session=database_session,
            tenant_id=access.tenant.id,
            employee_id=task.employee_id,
            can_manage_employees=role_has_permission(
                role=access.role, permission=TenantPermission.EMPLOYEES_MANAGE
            ),
        )
        _validate_input(definition, payload.input)
        updated = task.reschedule(
            schedule=_build_schedule(payload.schedule),
            input_data=payload.input,
            name=payload.name,
            misfire_policy=payload.misfire_policy,
            concurrency_policy=payload.concurrency_policy,
            max_retries=payload.max_retries,
            retry_backoff_seconds=payload.retry_backoff_seconds,
            now=datetime.now(UTC),
        )
        if updated.enabled:
            _ensure_no_future_occurrence(updated)
        await _save(
            database_session,
            task=updated,
            expected_revision=task.revision,
            actor_user_id=user.id,
            action="scheduled_task.updated",
        )
        await database_session.commit()
    return ScheduledTaskResponse.from_entity(updated)


@router.post("/{task_id}/pause", response_model=ScheduledTaskResponse)
async def pause_scheduled_task(
    task_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> ScheduledTaskResponse:
    return await _toggle(task_id, request, tenant_id=tenant_id, action="pause")


@router.post("/{task_id}/resume", response_model=ScheduledTaskResponse)
async def resume_scheduled_task(
    task_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> ScheduledTaskResponse:
    return await _toggle(task_id, request, tenant_id=tenant_id, action="resume")


async def _toggle(
    task_id: UUID, request: Request, *, tenant_id: UUID | None, action: str
) -> ScheduledTaskResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        task = await _load_own_task(
            database_session=database_session,
            tenant_id=access.tenant.id,
            task_id=task_id,
            user_id=user.id,
            is_manager=role_has_permission(
                role=access.role, permission=TenantPermission.RUNS_MANAGE
            ),
        )
        now = datetime.now(UTC)
        try:
            updated = task.pause(now=now) if action == "pause" else task.resume(now=now)
        except InvalidScheduledTaskTransition as error:
            raise _conflict("invalid_scheduled_task_transition", str(error)) from error
        await _save(
            database_session,
            task=updated,
            expected_revision=task.revision,
            actor_user_id=user.id,
            action=f"scheduled_task.{'paused' if action == 'pause' else 'resumed'}",
        )
        await database_session.commit()
    return ScheduledTaskResponse.from_entity(updated)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduled_task(
    task_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> None:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        task = await _load_own_task(
            database_session=database_session,
            tenant_id=access.tenant.id,
            task_id=task_id,
            user_id=user.id,
            is_manager=role_has_permission(
                role=access.role, permission=TenantPermission.RUNS_MANAGE
            ),
        )
        await SqlAlchemyScheduledTaskRepository(database_session).delete(
            tenant_id=access.tenant.id, task_id=task.id
        )
        await emit_audit_event(
            database_session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="scheduled_task.deleted",
            resource_type="scheduled_task",
            resource_id=task.id,
            metadata={"employee_id": str(task.employee_id)},
        )
        await database_session.commit()


@router.get("/{task_id}/executions", response_model=ScheduledTaskExecutionListResponse)
async def list_scheduled_task_executions(
    task_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScheduledTaskExecutionListResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        task = await _load_own_task(
            database_session=database_session,
            tenant_id=access.tenant.id,
            task_id=task_id,
            user_id=user.id,
            is_manager=role_has_permission(
                role=access.role, permission=TenantPermission.RUNS_MANAGE
            ),
        )
        items, total = await SqlAlchemyScheduledTaskExecutionRepository(
            database_session
        ).list_for_task(
            tenant_id=access.tenant.id,
            scheduled_task_id=task.id,
            limit=limit,
            offset=offset,
        )
    return ScheduledTaskExecutionListResponse(
        items=[ScheduledTaskExecutionResponse.from_entity(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _save(
    database_session: AsyncSession,
    *,
    task: ScheduledTask,
    expected_revision: int,
    actor_user_id: UUID,
    action: str,
) -> None:
    updated = await SqlAlchemyScheduledTaskRepository(database_session).update_with_cas(
        task, expected_revision=expected_revision
    )
    if not updated:
        raise _conflict("scheduled_task_conflict", "定时任务已被并发修改，请刷新后重试")
    await emit_audit_event(
        database_session,
        tenant_id=task.tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type="scheduled_task",
        resource_id=task.id,
        metadata={
            "employee_id": str(task.employee_id),
            "enabled": task.enabled,
            "schedule_kind": task.schedule.kind.value,
        },
    )
