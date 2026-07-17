"""审批中心 API（C13）：待办、详情、批准、拒绝、转交、撤回与历史。

RBAC 全部在服务端校验（ApprovalService）；前端隐藏入口只是体验优化。
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, JsonValue

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.approvals import (
    SqlAlchemyApprovalRepository,
    create_approval_service,
)
from agent_platform.infrastructure.database.repositories.auth import (
    SqlAlchemyUserRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyWorkspaceRepository,
)
from agent_platform.platform.approvals.entities import Approval, ApprovalStatus
from agent_platform.platform.approvals.errors import (
    ApprovalConcurrencyConflict,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalNotPending,
    ApprovalPermissionDenied,
    ApprovalReasonRequired,
    ApprovalRunNotActionable,
)
from agent_platform.platform.tenants.permissions import (
    TenantPermission,
    role_at_least,
    role_has_permission,
)

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
IdempotencyHeader = Annotated[UUID | None, Header(alias="Idempotency-Key")]

_HISTORY_STATUSES = (
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
    ApprovalStatus.WITHDRAWN,
    ApprovalStatus.TRANSFERRED,
)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    source: str
    approval_type: str
    risk_level: str
    status: str
    requested_by: UUID
    required_role: str
    context: dict[str, JsonValue]
    run_id: UUID | None
    invocation_id: UUID | None
    employee_id: UUID | None
    assignee_id: UUID | None
    decided_by: UUID | None
    reason: str | None
    decided_at: datetime | None
    created_at: datetime
    expires_at: datetime | None
    transferred_from_id: UUID | None
    transferred_to_id: UUID | None
    revision: int

    @classmethod
    def from_entity(cls, approval: Approval) -> "ApprovalResponse":
        # 展示层的有效状态：超时未决策的 pending 展示为 expired（读取时判定）。
        effective_status = (
            ApprovalStatus.EXPIRED.value
            if approval.is_expired(now=datetime.now(UTC))
            else approval.status.value
        )
        return cls(
            id=approval.id,
            tenant_id=approval.tenant_id,
            source=approval.source.value,
            approval_type=approval.approval_type,
            risk_level=approval.risk_level,
            status=effective_status,
            requested_by=approval.requested_by,
            required_role=approval.required_role.value,
            context=approval.context,
            run_id=approval.run_id,
            invocation_id=approval.invocation_id,
            employee_id=approval.employee_id,
            assignee_id=approval.assignee_id,
            decided_by=approval.decided_by,
            reason=approval.decision_reason,
            decided_at=approval.decided_at,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
            transferred_from_id=approval.transferred_from_id,
            transferred_to_id=approval.transferred_to_id,
            revision=approval.revision,
        )


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
    total: int
    limit: int
    offset: int


class DecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class TransferRequest(BaseModel):
    assignee_email: EmailStr
    reason: str | None = Field(default=None, max_length=2000)


class WithdrawRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _map_service_error(error: Exception) -> HTTPException:
    if isinstance(error, ApprovalNotFound):
        return _not_found()
    if isinstance(error, ApprovalPermissionDenied):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "permission_denied", "message": "没有执行此操作的权限"},
        )
    if isinstance(error, ApprovalExpired):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "approval_expired", "message": "审批已超时过期"},
        )
    if isinstance(error, ApprovalNotPending):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "approval_not_pending",
                "message": f"审批已处于 {error.status} 状态",
            },
        )
    if isinstance(error, ApprovalConcurrencyConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "approval_conflict", "message": "审批已被并发处理，请刷新后重试"},
        )
    if isinstance(error, ApprovalReasonRequired):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "approval_reason_required", "message": "拒绝审批必须填写理由"},
        )
    if isinstance(error, ApprovalRunNotActionable):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_run_transition",
                "message": "审批目标任务已不在等待审批状态",
            },
        )
    raise error


@router.get("", response_model=ApprovalListResponse)
async def list_approvals(
    request: Request,
    tenant_id: TenantHeader = None,
    view: Annotated[Literal["pending", "history"], Query()] = "pending",
    assignee_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalListResponse:
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
        statuses = (
            (ApprovalStatus.PENDING,) if view == "pending" else _HISTORY_STATUSES
        )
        items, total = await SqlAlchemyApprovalRepository(database_session).list(
            tenant_id=access.tenant.id,
            statuses=statuses,
            assignee_id=assignee_id if is_manager else None,
            visible_to=None if is_manager else user.id,
            include_unassigned=False,
            limit=limit,
            offset=offset,
        )
    return ApprovalListResponse(
        items=[ApprovalResponse.from_entity(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ApprovalResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        approval = await SqlAlchemyApprovalRepository(database_session).get(
            tenant_id=access.tenant.id, approval_id=approval_id
        )
        if approval is None:
            raise _not_found()
        is_manager = role_has_permission(
            role=access.role, permission=TenantPermission.RUNS_MANAGE
        )
        if not is_manager and user.id not in {approval.assignee_id, approval.requested_by}:
            raise _not_found()
    return ApprovalResponse.from_entity(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval(
    approval_id: UUID,
    payload: DecisionRequest,
    request: Request,
    tenant_id: TenantHeader = None,
    idempotency_key: IdempotencyHeader = None,
) -> ApprovalResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            approval = await create_approval_service(database_session).approve(
                tenant_id=access.tenant.id,
                approval_id=approval_id,
                actor_id=user.id,
                actor_role=access.role,
                reason=payload.reason,
                decision_key=idempotency_key,
            )
        except ApprovalExpired as error:
            # 惰性过期结算需要落库
            await database_session.commit()
            raise _map_service_error(error) from error
        except Exception as error:
            await database_session.rollback()
            raise _map_service_error(error) from error
        await database_session.commit()
    return ApprovalResponse.from_entity(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    approval_id: UUID,
    payload: RejectRequest,
    request: Request,
    tenant_id: TenantHeader = None,
    idempotency_key: IdempotencyHeader = None,
) -> ApprovalResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            approval = await create_approval_service(database_session).reject(
                tenant_id=access.tenant.id,
                approval_id=approval_id,
                actor_id=user.id,
                actor_role=access.role,
                reason=payload.reason,
                decision_key=idempotency_key,
            )
        except ApprovalExpired as error:
            # 惰性过期结算需要落库
            await database_session.commit()
            raise _map_service_error(error) from error
        except Exception as error:
            await database_session.rollback()
            raise _map_service_error(error) from error
        await database_session.commit()
    return ApprovalResponse.from_entity(approval)


@router.post("/{approval_id}/transfer", response_model=ApprovalResponse)
async def transfer_approval(
    approval_id: UUID,
    payload: TransferRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ApprovalResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        approvals = SqlAlchemyApprovalRepository(database_session)
        approval = await approvals.get(
            tenant_id=access.tenant.id, approval_id=approval_id
        )
        if approval is None:
            raise _not_found()
        assignee = await SqlAlchemyUserRepository(database_session).get_by_email(
            str(payload.assignee_email)
        )
        assignee_access = (
            await SqlAlchemyWorkspaceRepository(database_session).get_for_user(
                user_id=assignee.id, tenant_id=access.tenant.id
            )
            if assignee is not None
            else None
        )
        if (
            assignee is None
            or assignee_access is None
            or not role_at_least(
                role=assignee_access.role, minimum=approval.required_role
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "assignee_not_eligible",
                    "message": "被转交人不存在或角色不满足审批要求",
                },
            )
        try:
            child = await create_approval_service(database_session).transfer(
                tenant_id=access.tenant.id,
                approval_id=approval_id,
                actor_id=user.id,
                actor_role=access.role,
                assignee_id=assignee.id,
                assignee_role=assignee_access.role,
                reason=payload.reason,
            )
        except ApprovalExpired as error:
            # 惰性过期结算需要落库
            await database_session.commit()
            raise _map_service_error(error) from error
        except Exception as error:
            await database_session.rollback()
            raise _map_service_error(error) from error
        await database_session.commit()
    return ApprovalResponse.from_entity(child)


@router.post("/{approval_id}/withdraw", response_model=ApprovalResponse)
async def withdraw_approval(
    approval_id: UUID,
    payload: WithdrawRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ApprovalResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            approval = await create_approval_service(database_session).withdraw(
                tenant_id=access.tenant.id,
                approval_id=approval_id,
                actor_id=user.id,
                reason=payload.reason,
            )
        except ApprovalExpired as error:
            # 惰性过期结算需要落库
            await database_session.commit()
            raise _map_service_error(error) from error
        except Exception as error:
            await database_session.rollback()
            raise _map_service_error(error) from error
        await database_session.commit()
    return ApprovalResponse.from_entity(approval)
