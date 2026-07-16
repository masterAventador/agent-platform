from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from agent_platform.api.dependencies.authentication import build_auth_service
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyWorkspaceRepository,
)
from agent_platform.platform.auth.errors import (
    AuthenticationRequired,
    InvalidCredentials,
    RateLimitExceeded,
    RegistrationUnavailable,
)
from agent_platform.platform.tenants.memberships import WorkspaceAccess
from agent_platform.platform.tenants.permissions import permissions_for_role
from agent_platform.platform.users.entities import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: Annotated[str, Field(min_length=12, max_length=128)]


class UserResponse(BaseModel):
    id: UUID
    email: str
    email_verified: bool
    workspaces: list["WorkspaceResponse"]

    @classmethod
    def from_entity(cls, user: User, workspaces: list[WorkspaceAccess]) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            email_verified=user.email_verified,
            workspaces=[WorkspaceResponse.from_access(access) for access in workspaces],
        )


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    role: str
    permissions: list[str]

    @classmethod
    def from_access(cls, access: WorkspaceAccess) -> "WorkspaceResponse":
        return cls(
            id=access.tenant.id,
            name=access.tenant.name,
            slug=access.tenant.slug,
            role=access.role.value,
            permissions=sorted(
                permission.value for permission in permissions_for_role(access.role)
            ),
        )


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _raise_auth_error(error: Exception) -> None:
    if isinstance(error, RegistrationUnavailable):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "registration_unavailable", "message": "无法使用该邮箱注册"},
        ) from error
    if isinstance(error, InvalidCredentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_credentials", "message": "邮箱或密码错误"},
        ) from error
    if isinstance(error, AuthenticationRequired):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "请先登录"},
        ) from error
    if isinstance(error, RateLimitExceeded):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_exceeded", "message": "请求过于频繁，请稍后重试"},
        ) from error
    raise error


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: CredentialsRequest, request: Request) -> UserResponse:
    async with request.app.state.session_factory() as database_session:
        try:
            await request.app.state.auth_rate_limiter.ensure_allowed(
                scope="register_ip",
                key=_client_key(request),
            )
            user = await build_auth_service(request, database_session).register(
                email=str(payload.email),
                password=payload.password,
            )
            workspaces = await SqlAlchemyWorkspaceRepository(database_session).list_for_user(
                user.id
            )
            if workspaces:
                await emit_audit_event(
                    database_session,
                    tenant_id=workspaces[0].tenant.id,
                    actor_user_id=user.id,
                    action="tenant.member_added",
                    resource_type="tenant_membership",
                    resource_id=user.id,
                    metadata={"role": workspaces[0].role.value},
                )
                await emit_audit_event(
                    database_session,
                    tenant_id=workspaces[0].tenant.id,
                    actor_user_id=user.id,
                    action="tenant.role_assigned",
                    resource_type="tenant_membership",
                    resource_id=user.id,
                    metadata={
                        "role": workspaces[0].role.value,
                        "permission_count": len(permissions_for_role(workspaces[0].role)),
                    },
                )
                await emit_audit_event(
                    database_session,
                    tenant_id=workspaces[0].tenant.id,
                    actor_user_id=user.id,
                    action="auth.registered",
                    resource_type="user",
                    resource_id=user.id,
                    metadata={"workspace_count": len(workspaces)},
                )
            await database_session.commit()
        except (
            RegistrationUnavailable,
            RateLimitExceeded,
        ) as error:
            _raise_auth_error(error)
            raise AssertionError("unreachable") from error
    return UserResponse.from_entity(user, workspaces)


@router.post("/login", response_model=UserResponse)
async def login(payload: CredentialsRequest, request: Request, response: Response) -> UserResponse:
    async with request.app.state.session_factory() as database_session:
        try:
            await request.app.state.auth_rate_limiter.ensure_allowed(
                scope="login_ip",
                key=_client_key(request),
            )
            issued_session = await build_auth_service(request, database_session).login(
                email=str(payload.email),
                password=payload.password,
            )
            workspaces = await SqlAlchemyWorkspaceRepository(database_session).list_for_user(
                issued_session.user.id
            )
            if workspaces:
                await emit_audit_event(
                    database_session,
                    tenant_id=workspaces[0].tenant.id,
                    actor_user_id=issued_session.user.id,
                    action="auth.login_succeeded",
                    resource_type="user",
                    resource_id=issued_session.user.id,
                    metadata={"workspace_count": len(workspaces)},
                )
            await database_session.commit()
        except (InvalidCredentials, RateLimitExceeded) as error:
            _raise_auth_error(error)
            raise AssertionError("unreachable") from error

    settings = request.app.state.settings
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=issued_session.raw_token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_same_site,
        path="/",
    )
    return UserResponse.from_entity(issued_session.user, workspaces)


@router.get("/me", response_model=UserResponse)
async def current_user(request: Request) -> UserResponse:
    raw_token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    async with request.app.state.session_factory() as database_session:
        try:
            user = await build_auth_service(request, database_session).authenticate(raw_token)
            workspaces = await SqlAlchemyWorkspaceRepository(database_session).list_for_user(
                user.id
            )
        except AuthenticationRequired as error:
            _raise_auth_error(error)
            raise AssertionError("unreachable") from error
    return UserResponse.from_entity(user, workspaces)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    async with request.app.state.session_factory() as database_session:
        await build_auth_service(request, database_session).logout(raw_token)
        await database_session.commit()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_same_site,
        path="/",
    )
