from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.infrastructure.database.repositories.auth import (
    SqlAlchemyAuthSessionRepository,
    SqlAlchemyUserRepository,
)
from agent_platform.platform.auth.errors import (
    AuthenticationRequired,
    InvalidCredentials,
    RateLimitExceeded,
    RegistrationUnavailable,
)
from agent_platform.platform.auth.services import AuthService
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

    @classmethod
    def from_entity(cls, user: User) -> "UserResponse":
        return cls(id=user.id, email=user.email, email_verified=user.email_verified)


def _service(request: Request, database_session: AsyncSession) -> AuthService:
    return AuthService(
        users=SqlAlchemyUserRepository(database_session),
        sessions=SqlAlchemyAuthSessionRepository(database_session),
        password_hasher=request.app.state.password_hasher,
        rate_limiter=request.app.state.auth_rate_limiter,
        token_manager=request.app.state.session_token_manager,
        session_ttl_seconds=request.app.state.settings.auth_session_ttl_seconds,
        require_email_verification=request.app.state.settings.require_email_verification,
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
            user = await _service(request, database_session).register(
                email=str(payload.email),
                password=payload.password,
            )
        except (
            RegistrationUnavailable,
            RateLimitExceeded,
        ) as error:
            _raise_auth_error(error)
            raise AssertionError("unreachable") from error
    return UserResponse.from_entity(user)


@router.post("/login", response_model=UserResponse)
async def login(payload: CredentialsRequest, request: Request, response: Response) -> UserResponse:
    async with request.app.state.session_factory() as database_session:
        try:
            await request.app.state.auth_rate_limiter.ensure_allowed(
                scope="login_ip",
                key=_client_key(request),
            )
            issued_session = await _service(request, database_session).login(
                email=str(payload.email),
                password=payload.password,
            )
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
        samesite="lax",
        path="/",
    )
    return UserResponse.from_entity(issued_session.user)


@router.get("/me", response_model=UserResponse)
async def current_user(request: Request) -> UserResponse:
    raw_token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    async with request.app.state.session_factory() as database_session:
        try:
            user = await _service(request, database_session).authenticate(raw_token)
        except AuthenticationRequired as error:
            _raise_auth_error(error)
            raise AssertionError("unreachable") from error
    return UserResponse.from_entity(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    async with request.app.state.session_factory() as database_session:
        await _service(request, database_session).logout(raw_token)
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
