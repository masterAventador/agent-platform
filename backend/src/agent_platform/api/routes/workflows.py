"""工作流注册中心 API（C11）：注册、加版本、发布、回滚、列表与版本查询。

RBAC 复用 EMPLOYEES_MANAGE（工作流属于员工编辑器域）；所有写操作接入 C14 平台审计。
图定义只做平台自研静态校验，不外泄 LangGraph 内部结构。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.workflows import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowVersionRepository,
)
from agent_platform.platform.tenants.permissions import TenantPermission
from agent_platform.platform.workflows.entities import Workflow, WorkflowVersion
from agent_platform.platform.workflows.errors import (
    WorkflowNameAlreadyExists,
    WorkflowNotFound,
    WorkflowNotPublished,
    WorkflowVersionAlreadyExists,
    WorkflowVersionNotFound,
)
from agent_platform.platform.workflows.graph_spec import InvalidWorkflowGraph
from agent_platform.platform.workflows.services import WorkflowService

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    description: str
    latest_version: int
    published_version: int | None
    status: str

    @classmethod
    def from_entity(cls, workflow: Workflow) -> "WorkflowResponse":
        return cls(
            id=workflow.id,
            tenant_id=workflow.tenant_id,
            name=workflow.name,
            description=workflow.description,
            latest_version=workflow.latest_version,
            published_version=workflow.published_version,
            status=workflow.status.value,
        )


class WorkflowVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    description: str
    graph: dict[str, object]
    created_at: datetime
    published_at: datetime | None

    @classmethod
    def from_entity(cls, version: WorkflowVersion) -> "WorkflowVersionResponse":
        return cls(
            version=version.version,
            description=version.description,
            graph=version.graph,
            created_at=version.created_at,
            published_at=version.published_at,
        )


class RegisterWorkflowRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=2000)] = ""
    graph: dict[str, object]


class AddWorkflowVersionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: Annotated[str, Field(max_length=2000)] = ""
    graph: dict[str, object]


class WorkflowVersionSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Annotated[int, Field(ge=1)]


def _service(session: AsyncSession) -> WorkflowService:
    return WorkflowService(
        workflows=SqlAlchemyWorkflowRepository(session),
        versions=SqlAlchemyWorkflowVersionRepository(session),
    )


def _raise_workflow_error(error: Exception) -> None:
    if isinstance(error, InvalidWorkflowGraph):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_workflow_graph",
                "message": "工作流图定义无效",
                "path": list(error.issue.path),
                "reason": error.issue.message,
            },
        ) from error
    if isinstance(error, WorkflowNameAlreadyExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "workflow_name_exists", "message": "已存在同名工作流"},
        ) from error
    if isinstance(error, WorkflowVersionAlreadyExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "workflow_version_conflict", "message": "工作流版本号冲突，请重试"},
        ) from error
    if isinstance(error, WorkflowNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workflow_not_found", "message": "工作流不存在"},
        ) from error
    if isinstance(error, WorkflowVersionNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "workflow_version_not_found", "message": "工作流版本不存在"},
        ) from error
    if isinstance(error, WorkflowNotPublished):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "workflow_not_published", "message": "工作流尚未发布"},
        ) from error
    raise error


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def register_workflow(
    payload: RegisterWorkflowRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> WorkflowResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        try:
            workflow = await _service(session).register(
                tenant_id=access.tenant.id,
                created_by=user.id,
                name=payload.name,
                description=payload.description,
                graph=payload.graph,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="workflow.registered",
                resource_type="workflow",
                resource_id=workflow.id,
                metadata={"name": workflow.name},
            )
            await session.commit()
        except (InvalidWorkflowGraph, WorkflowNameAlreadyExists) as error:
            _raise_workflow_error(error)
            raise AssertionError("unreachable") from error
    return WorkflowResponse.from_entity(workflow)


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[WorkflowResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        workflows = await _service(session).list_all(tenant_id=access.tenant.id)
    return [WorkflowResponse.from_entity(workflow) for workflow in workflows]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> WorkflowResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        try:
            workflow = await _service(session).get(
                tenant_id=access.tenant.id, workflow_id=workflow_id
            )
        except WorkflowNotFound as error:
            _raise_workflow_error(error)
            raise AssertionError("unreachable") from error
    return WorkflowResponse.from_entity(workflow)


@router.post("/{workflow_id}/versions", response_model=WorkflowResponse)
async def add_workflow_version(
    workflow_id: UUID,
    payload: AddWorkflowVersionRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> WorkflowResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        try:
            workflow = await _service(session).add_version(
                tenant_id=access.tenant.id,
                workflow_id=workflow_id,
                created_by=user.id,
                graph=payload.graph,
                description=payload.description,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="workflow.version_added",
                resource_type="workflow",
                resource_id=workflow.id,
                metadata={"version": workflow.latest_version},
            )
            await session.commit()
        except (InvalidWorkflowGraph, WorkflowNotFound, WorkflowVersionAlreadyExists) as error:
            _raise_workflow_error(error)
            raise AssertionError("unreachable") from error
    return WorkflowResponse.from_entity(workflow)


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionResponse])
async def list_workflow_versions(
    workflow_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[WorkflowVersionResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        try:
            versions = await _service(session).list_versions(
                tenant_id=access.tenant.id, workflow_id=workflow_id
            )
        except WorkflowNotFound as error:
            _raise_workflow_error(error)
            raise AssertionError("unreachable") from error
    return [WorkflowVersionResponse.from_entity(version) for version in versions]


@router.post("/{workflow_id}/publish", response_model=WorkflowResponse)
async def publish_workflow(
    workflow_id: UUID,
    payload: WorkflowVersionSelector,
    request: Request,
    tenant_id: TenantHeader = None,
) -> WorkflowResponse:
    return await _set_published(
        workflow_id=workflow_id,
        version=payload.version,
        request=request,
        tenant_id=tenant_id,
        action="workflow.published",
        rollback=False,
    )


@router.post("/{workflow_id}/rollback", response_model=WorkflowResponse)
async def rollback_workflow(
    workflow_id: UUID,
    payload: WorkflowVersionSelector,
    request: Request,
    tenant_id: TenantHeader = None,
) -> WorkflowResponse:
    return await _set_published(
        workflow_id=workflow_id,
        version=payload.version,
        request=request,
        tenant_id=tenant_id,
        action="workflow.rolled_back",
        rollback=True,
    )


async def _set_published(
    *,
    workflow_id: UUID,
    version: int,
    request: Request,
    tenant_id: UUID | None,
    action: str,
    rollback: bool,
) -> WorkflowResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.EMPLOYEES_MANAGE,
        )
        service = _service(session)
        try:
            if rollback:
                workflow = await service.rollback(
                    tenant_id=access.tenant.id,
                    workflow_id=workflow_id,
                    version=version,
                    rolled_back_by=user.id,
                )
            else:
                workflow = await service.publish(
                    tenant_id=access.tenant.id,
                    workflow_id=workflow_id,
                    version=version,
                    published_by=user.id,
                )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action=action,
                resource_type="workflow",
                resource_id=workflow.id,
                metadata={"published_version": workflow.published_version},
            )
            await session.commit()
        except (WorkflowNotFound, WorkflowVersionNotFound) as error:
            _raise_workflow_error(error)
            raise AssertionError("unreachable") from error
    return WorkflowResponse.from_entity(workflow)
