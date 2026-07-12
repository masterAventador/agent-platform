from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.infrastructure.database.repositories.auth import (
    SqlAlchemyAuthSessionRepository,
    SqlAlchemyUserRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyWorkspaceRepository,
)
from agent_platform.platform.auth.errors import AuthenticationRequired
from agent_platform.platform.auth.services import AuthService
from agent_platform.platform.tenants.memberships import TenantRole, WorkspaceAccess
from agent_platform.platform.users.entities import User


def build_auth_service(request: Request, database_session: AsyncSession) -> AuthService:
    return AuthService(
        users=SqlAlchemyUserRepository(database_session),
        sessions=SqlAlchemyAuthSessionRepository(database_session),
        password_hasher=request.app.state.password_hasher,
        rate_limiter=request.app.state.auth_rate_limiter,
        token_manager=request.app.state.session_token_manager,
        session_ttl_seconds=request.app.state.settings.auth_session_ttl_seconds,
        require_email_verification=request.app.state.settings.require_email_verification,
        workspaces=SqlAlchemyWorkspaceRepository(database_session),
    )


async def authenticate_request(request: Request, database_session: AsyncSession) -> User:
    raw_token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    try:
        return await build_auth_service(request, database_session).authenticate(raw_token)
    except AuthenticationRequired as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "请先登录"},
        ) from error


async def resolve_workspace(
    *,
    request: Request,
    database_session: AsyncSession,
    tenant_id: UUID | None,
    owner_required: bool,
) -> tuple[User, WorkspaceAccess]:
    user = await authenticate_request(request, database_session)
    workspaces = SqlAlchemyWorkspaceRepository(database_session)
    if tenant_id is None:
        available = await workspaces.list_for_user(user.id)
        if len(available) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "tenant_required", "message": "请选择企业工作区"},
            )
        access = available[0]
    else:
        maybe_access = await workspaces.get_for_user(user_id=user.id, tenant_id=tenant_id)
        if maybe_access is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "resource_not_found", "message": "资源不存在"},
            )
        access = maybe_access

    if owner_required and access.role is not TenantRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "permission_denied", "message": "没有执行此操作的权限"},
        )
    return user, access
