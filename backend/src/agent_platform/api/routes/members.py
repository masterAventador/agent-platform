"""C15 企业成员与邀请管理 API。

成员管理面（企业设置、成员列表、角色变更、移除、Owner 转移、邀请创建/列表/撤销）
需要 ``workspace.manage``（Owner）资源级权限；邀请接受/拒绝由被邀请者本人凭 token
执行。角色变更/移除/转 Owner 在租户成员行锁内运行领域校验并落库，配合真实
PostgreSQL 消除并发绕过最后一个 Owner 保护的竞态。所有管理动作接入 C14 审计。
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from agent_platform.api.dependencies.authentication import (
    authenticate_request,
    resolve_workspace,
)
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.invitations import (
    SqlAlchemyInvitationRepository,
)
from agent_platform.infrastructure.database.repositories.memberships import (
    MemberDetail,
    SqlAlchemyMembershipRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
)
from agent_platform.platform.tenants.errors import (
    AlreadyMember,
    CannotRemoveSelf,
    InvalidRoleAssignment,
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotFound,
    InvitationNotPending,
    InvitationRoleNotAllowed,
    LastOwnerProtected,
    MembershipNotFound,
)
from agent_platform.platform.tenants.invitations import TenantInvitation
from agent_platform.platform.tenants.member_management import (
    INVITABLE_ROLES,
    ensure_role_is_invitable,
    plan_owner_transfer,
    validate_removal,
    validate_role_change,
)
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import TenantPermission

router = APIRouter(prefix="/api/v1/tenant", tags=["members"])
invitation_router = APIRouter(prefix="/api/v1/invitations", tags=["invitations"])

TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class TenantSettingsRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: Annotated[str, Field(min_length=1, max_length=200)]


class TenantSettingsResponse(BaseModel):
    id: UUID
    name: str
    slug: str


class MemberResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str | None
    role: str
    joined_at: str

    @classmethod
    def from_detail(cls, detail: MemberDetail) -> "MemberResponse":
        return cls(
            user_id=detail.user_id,
            email=detail.email,
            display_name=detail.display_name,
            role=detail.role.value,
            joined_at=detail.joined_at.isoformat(),
        )


class RoleChangeRequest(BaseModel):
    role: TenantRole


class TransferOwnerRequest(BaseModel):
    user_id: UUID


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    role: TenantRole


class InvitationResponse(BaseModel):
    id: UUID
    email: str
    role: str
    status: str
    created_at: str
    expires_at: str

    @classmethod
    def from_entity(cls, invitation: TenantInvitation) -> "InvitationResponse":
        return cls(
            id=invitation.id,
            email=invitation.email,
            role=invitation.role.value,
            status=invitation.status.value,
            created_at=invitation.created_at.isoformat(),
            expires_at=invitation.expires_at.isoformat(),
        )


class InvitationCreatedResponse(InvitationResponse):
    token: str


class InvitationTokenRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token: Annotated[str, Field(min_length=1, max_length=256)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _raise_member_error(error: Exception) -> None:
    if isinstance(error, MembershipNotFound):
        raise _error(status.HTTP_404_NOT_FOUND, "resource_not_found", "成员不存在") from error
    if isinstance(error, LastOwnerProtected):
        raise _error(
            status.HTTP_409_CONFLICT,
            "last_owner_protected",
            "企业必须至少保留一个 Owner",
        ) from error
    if isinstance(error, CannotRemoveSelf):
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "cannot_remove_self",
            "不能移除自己，请使用 Owner 转移",
        ) from error
    if isinstance(error, InvalidRoleAssignment):
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_role_assignment",
            str(error),
        ) from error
    raise error


# --------------------------------------------------------------------------- #
# 企业设置
# --------------------------------------------------------------------------- #


@router.patch("/settings", response_model=TenantSettingsResponse)
async def update_tenant_settings(
    payload: TenantSettingsRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> TenantSettingsResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        tenants = SqlAlchemyTenantRepository(session)
        await tenants.rename(tenant_id=access.tenant.id, name=payload.name)
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="tenant.settings_updated",
            resource_type="tenant",
            resource_id=access.tenant.id,
            metadata={"name": payload.name},
        )
        renamed = await tenants.get_by_id(access.tenant.id)
        await session.commit()
    assert renamed is not None
    return TenantSettingsResponse(id=renamed.id, name=renamed.name, slug=renamed.slug)


# --------------------------------------------------------------------------- #
# 成员管理
# --------------------------------------------------------------------------- #


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[MemberResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        members = await SqlAlchemyMembershipRepository(session).list_members(access.tenant.id)
    return [MemberResponse.from_detail(detail) for detail in members]


@router.patch("/members/{target_user_id}/role", response_model=MemberResponse)
async def change_member_role(
    target_user_id: UUID,
    payload: RoleChangeRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MemberResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        # 与 create_invitation 语义对齐（同一 INVITABLE_ROLES 集合）：Owner 不能经角色
        # 变更直接授予，只能走显式的所有权转移接口，避免绕过 Owner 唯一性不变式。
        if payload.role not in INVITABLE_ROLES:
            raise _error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "owner_role_requires_transfer",
                "Owner 只能通过所有权转移产生",
            )
        repository = SqlAlchemyMembershipRepository(session)
        locked = await repository.lock_members(access.tenant.id)
        try:
            validate_role_change(
                members=locked,
                target_id=target_user_id,
                new_role=payload.role,
            )
        except (MembershipNotFound, LastOwnerProtected) as error:
            _raise_member_error(error)
        await repository.set_role(
            tenant_id=access.tenant.id,
            user_id=target_user_id,
            role=payload.role,
        )
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="tenant.role_assigned",
            resource_type="tenant_membership",
            resource_id=target_user_id,
            metadata={"role": payload.role.value},
        )
        members = await repository.list_members(access.tenant.id)
        await session.commit()
    detail = next(m for m in members if m.user_id == target_user_id)
    return MemberResponse.from_detail(detail)


@router.delete("/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    target_user_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> Response:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        repository = SqlAlchemyMembershipRepository(session)
        locked = await repository.lock_members(access.tenant.id)
        try:
            validate_removal(members=locked, actor_id=user.id, target_id=target_user_id)
        except (MembershipNotFound, CannotRemoveSelf, LastOwnerProtected) as error:
            _raise_member_error(error)
        await repository.remove(tenant_id=access.tenant.id, user_id=target_user_id)
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="tenant.member_removed",
            resource_type="tenant_membership",
            resource_id=target_user_id,
            metadata={},
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/members/transfer-owner", status_code=status.HTTP_200_OK)
async def transfer_owner(
    payload: TransferOwnerRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> dict[str, str]:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        repository = SqlAlchemyMembershipRepository(session)
        locked = await repository.lock_members(access.tenant.id)
        try:
            changes = plan_owner_transfer(
                members=locked,
                actor_id=user.id,
                target_id=payload.user_id,
            )
        except (MembershipNotFound, InvalidRoleAssignment) as error:
            _raise_member_error(error)
        for change in changes:
            await repository.set_role(
                tenant_id=access.tenant.id,
                user_id=change.user_id,
                role=change.role,
            )
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="tenant.ownership_transferred",
            resource_type="tenant_membership",
            resource_id=payload.user_id,
            metadata={
                "new_owner_id": str(payload.user_id),
                "previous_owner_id": str(user.id),
            },
        )
        await session.commit()
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# 邀请（管理侧）
# --------------------------------------------------------------------------- #


@router.post(
    "/invitations",
    response_model=InvitationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    payload: InvitationCreateRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> InvitationCreatedResponse:
    try:
        ensure_role_is_invitable(payload.role)
    except InvitationRoleNotAllowed as error:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invitation_role_not_allowed",
            "邀请只能授予 Admin 或 Member 角色",
        ) from error

    raw_token, token_digest = request.app.state.session_token_manager.issue()
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        invitation = TenantInvitation.issue(
            tenant_id=access.tenant.id,
            email=str(payload.email),
            role=payload.role,
            token_digest=token_digest,
            invited_by=user.id,
            ttl_seconds=request.app.state.settings.account_invitation_ttl_seconds,
        )
        await SqlAlchemyInvitationRepository(session).add(invitation)
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="tenant.invitation_created",
            resource_type="tenant_invitation",
            resource_id=invitation.id,
            metadata={"email": invitation.email, "role": invitation.role.value},
        )
        await session.commit()
    return InvitationCreatedResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role.value,
        status=invitation.status.value,
        created_at=invitation.created_at.isoformat(),
        expires_at=invitation.expires_at.isoformat(),
        token=raw_token,
    )


@router.get("/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[InvitationResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        invitations = await SqlAlchemyInvitationRepository(session).list_pending(
            access.tenant.id
        )
    return [InvitationResponse.from_entity(invitation) for invitation in invitations]


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> Response:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        repository = SqlAlchemyInvitationRepository(session)
        invitation = await repository.get_for_update(
            tenant_id=access.tenant.id,
            invitation_id=invitation_id,
        )
        if invitation is None:
            raise _error(status.HTTP_404_NOT_FOUND, "resource_not_found", "邀请不存在")
        try:
            revoked = invitation.revoke()
        except InvitationNotPending as error:
            raise _error(
                status.HTTP_409_CONFLICT,
                "invitation_not_pending",
                "邀请已处理，无法撤销",
            ) from error
        await repository.save(revoked)
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="tenant.invitation_revoked",
            resource_type="tenant_invitation",
            resource_id=invitation_id,
            metadata={},
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# 邀请（被邀请者侧）
# --------------------------------------------------------------------------- #


def _raise_invitation_response_error(error: Exception) -> None:
    if isinstance(error, InvitationNotFound):
        raise _error(status.HTTP_404_NOT_FOUND, "invitation_not_found", "邀请不存在") from error
    if isinstance(error, InvitationNotPending):
        raise _error(
            status.HTTP_409_CONFLICT,
            "invitation_not_pending",
            "邀请已处理或已撤销",
        ) from error
    if isinstance(error, InvitationExpired):
        raise _error(status.HTTP_410_GONE, "invitation_expired", "邀请已过期") from error
    if isinstance(error, InvitationEmailMismatch):
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "invitation_email_mismatch",
            "当前登录邮箱与邀请邮箱不一致",
        ) from error
    if isinstance(error, AlreadyMember):
        raise _error(
            status.HTTP_409_CONFLICT,
            "already_member",
            "你已经是该企业成员",
        ) from error
    raise error


@invitation_router.post("/accept", status_code=status.HTTP_200_OK)
async def accept_invitation(
    payload: InvitationTokenRequest,
    request: Request,
) -> dict[str, str]:
    token_digest = request.app.state.session_token_manager.digest(payload.token)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        repository = SqlAlchemyInvitationRepository(session)
        invitation = await repository.get_by_token_digest_for_update(token_digest)
        if invitation is None:
            raise _error(status.HTTP_404_NOT_FOUND, "invitation_not_found", "邀请不存在")
        try:
            accepted = invitation.accept(
                user_id=user.id,
                user_email=user.email,
                now=datetime.now(UTC),
            )
            await repository.save(accepted)
            await SqlAlchemyMembershipRepository(session).add(
                tenant_id=invitation.tenant_id,
                user_id=user.id,
                role=invitation.role,
            )
        except (
            InvitationNotPending,
            InvitationExpired,
            InvitationEmailMismatch,
            AlreadyMember,
        ) as error:
            _raise_invitation_response_error(error)
        await emit_audit_event(
            session,
            tenant_id=invitation.tenant_id,
            actor_user_id=user.id,
            action="tenant.invitation_accepted",
            resource_type="tenant_invitation",
            resource_id=invitation.id,
            metadata={"role": invitation.role.value},
        )
        await emit_audit_event(
            session,
            tenant_id=invitation.tenant_id,
            actor_user_id=user.id,
            action="tenant.member_added",
            resource_type="tenant_membership",
            resource_id=user.id,
            metadata={"role": invitation.role.value},
        )
        await session.commit()
    return {"status": "accepted", "tenant_id": str(invitation.tenant_id)}


@invitation_router.post("/reject", status_code=status.HTTP_200_OK)
async def reject_invitation(
    payload: InvitationTokenRequest,
    request: Request,
) -> dict[str, str]:
    token_digest = request.app.state.session_token_manager.digest(payload.token)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        repository = SqlAlchemyInvitationRepository(session)
        invitation = await repository.get_by_token_digest_for_update(token_digest)
        if invitation is None:
            raise _error(status.HTTP_404_NOT_FOUND, "invitation_not_found", "邀请不存在")
        try:
            rejected = invitation.reject(now=datetime.now(UTC))
        except (InvitationNotPending, InvitationExpired) as error:
            _raise_invitation_response_error(error)
        await repository.save(rejected)
        await emit_audit_event(
            session,
            tenant_id=invitation.tenant_id,
            actor_user_id=user.id,
            action="tenant.invitation_rejected",
            resource_type="tenant_invitation",
            resource_id=invitation.id,
            metadata={},
        )
        await session.commit()
    return {"status": "rejected"}
