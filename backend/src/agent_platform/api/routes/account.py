"""C15 账号体系 API：资料、修改密码、邮箱验证、找回密码、会话/设备管理。

资料/密码/会话操作只作用于当前登录用户自身（认证即授权）。找回密码请求端点的
防枚举主防线是「响应体/状态码逐字节恒等（202）+ 端点限流」：无论账号是否存在都
返回相同响应，并对请求邮箱与客户端来源限流，遏制高频采样。**不声称严格 constant-time**：
账号存在分支会做真实 DB 往返与持久化，仅提供尽力而为的等价补偿，无法保证时序恒定，
时序旁道由限流兜底。Demo 阶段不真发信，token 明文只通过独立开发通道端点读取，且该
通道在 staging/production 关闭。修改密码撤销其它会话、重置密码撤销全部会话，撤销后旧
token 立即失效。所有账号管理动作接入 C14 审计（脱敏，按用户主工作区归属租户）。
"""

import logging
import secrets
from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import authenticate_request
from agent_platform.infrastructure.database.repositories.account_tokens import (
    SqlAlchemyAccountTokenRepository,
)
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.auth import (
    SqlAlchemyAuthSessionRepository,
    SqlAlchemyUserRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyWorkspaceRepository,
)
from agent_platform.platform.accounts.errors import TokenInvalidOrExpired
from agent_platform.platform.accounts.tokens import (
    AccountTokenPurpose,
    OneTimeToken,
)
from agent_platform.platform.auth.entities import AuthSession
from agent_platform.platform.auth.errors import RateLimitExceeded
from agent_platform.platform.users.entities import User, normalize_display_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/account", tags=["account"])


class ProfileResponse(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    email_verified: bool

    @classmethod
    def from_entity(cls, user: User) -> "ProfileResponse":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            email_verified=user.email_verified,
        )


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: Annotated[str | None, Field(default=None, max_length=120)]


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Annotated[str, Field(min_length=12, max_length=128)]


class EmailVerificationTokenResponse(BaseModel):
    token: str | None


class ConfirmTokenRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token: Annotated[str, Field(min_length=1, max_length=256)]


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    token: Annotated[str, Field(min_length=1, max_length=256)]
    new_password: Annotated[str, Field(min_length=12, max_length=128)]


class SessionResponse(BaseModel):
    id: UUID
    created_at: str
    expires_at: str
    revoked: bool
    active: bool
    current: bool
    user_agent: str | None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _current_token_digest(request: Request) -> str | None:
    raw_token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    if raw_token is None:
        return None
    return str(request.app.state.session_token_manager.digest(raw_token))


async def _primary_tenant_id(session: AsyncSession, user_id: UUID) -> UUID | None:
    workspaces = await SqlAlchemyWorkspaceRepository(session).list_for_user(user_id)
    return workspaces[0].tenant.id if workspaces else None


async def _audit_account_event(
    session: AsyncSession,
    *,
    user_id: UUID,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> None:
    tenant_id = await _primary_tenant_id(session, user_id)
    if tenant_id is None:
        # 审计是租户内递增哈希链，无主工作区用户没有可归属租户；此时不静默丢弃，
        # 落受控告警日志留痕，避免账号管理操作出现无审计的静默缺口。
        logger.warning(
            "account_audit_skipped_no_workspace",
            extra={"account_action": action, "actor_user_id": str(user_id)},
        )
        return
    await emit_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata or {},
    )


# --------------------------------------------------------------------------- #
# 资料
# --------------------------------------------------------------------------- #


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(request: Request) -> ProfileResponse:
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
    return ProfileResponse.from_entity(user)


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(payload: ProfileUpdateRequest, request: Request) -> ProfileResponse:
    display_name = normalize_display_name(payload.display_name)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        users = SqlAlchemyUserRepository(session)
        await users.update_profile(user_id=user.id, display_name=display_name)
        await _audit_account_event(
            session,
            user_id=user.id,
            action="account.profile_updated",
            resource_type="user",
            resource_id=user.id,
        )
        updated = await users.get_by_id(user.id)
        await session.commit()
    assert updated is not None
    return ProfileResponse.from_entity(updated)


# --------------------------------------------------------------------------- #
# 修改密码
# --------------------------------------------------------------------------- #


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(payload: ChangePasswordRequest, request: Request) -> Response:
    hasher = request.app.state.password_hasher
    current_digest = _current_token_digest(request)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        if not hasher.verify(payload.current_password, user.password_hash):
            raise _error(
                status.HTTP_401_UNAUTHORIZED,
                "invalid_current_password",
                "当前密码不正确",
            )
        users = SqlAlchemyUserRepository(session)
        await users.set_password_hash(
            user_id=user.id,
            password_hash=hasher.hash(payload.new_password),
        )
        sessions = SqlAlchemyAuthSessionRepository(session)
        current_session_id = await _resolve_session_id(sessions, user.id, current_digest)
        await sessions.revoke_all_for_user(
            user_id=user.id,
            except_session_id=current_session_id,
        )
        await _audit_account_event(
            session,
            user_id=user.id,
            action="account.password_changed",
            resource_type="user",
            resource_id=user.id,
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# 邮箱验证
# --------------------------------------------------------------------------- #


@router.post("/email-verification/request", response_model=EmailVerificationTokenResponse)
async def request_email_verification(request: Request) -> EmailVerificationTokenResponse:
    settings = request.app.state.settings
    raw_token, token_digest = request.app.state.session_token_manager.issue()
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        token = OneTimeToken.issue(
            user_id=user.id,
            purpose=AccountTokenPurpose.EMAIL_VERIFICATION,
            token_digest=token_digest,
            ttl_seconds=settings.account_email_verification_ttl_seconds,
        )
        await SqlAlchemyAccountTokenRepository(session).add(token)
        await _audit_account_event(
            session,
            user_id=user.id,
            action="account.email_verification_requested",
            resource_type="user",
            resource_id=user.id,
        )
        await session.commit()
    # 已认证用户为自己请求验证 token，返回给自己不构成枚举风险；开发不发信时以此为准。
    return EmailVerificationTokenResponse(
        token=raw_token if settings.expose_dev_account_tokens else None
    )


@router.post("/email-verification/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email_verification(payload: ConfirmTokenRequest, request: Request) -> Response:
    token_digest = request.app.state.session_token_manager.digest(payload.token)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        tokens = SqlAlchemyAccountTokenRepository(session)
        record = await tokens.get_by_token_digest_for_update(
            purpose=AccountTokenPurpose.EMAIL_VERIFICATION,
            token_digest=token_digest,
        )
        if record is None or record.user_id != user.id:
            raise _error(
                status.HTTP_400_BAD_REQUEST,
                "token_invalid_or_expired",
                "验证链接无效或已过期",
            )
        try:
            consumed = record.consume()
        except TokenInvalidOrExpired as error:
            raise _error(
                status.HTTP_400_BAD_REQUEST,
                "token_invalid_or_expired",
                "验证链接无效或已过期",
            ) from error
        await tokens.save(consumed)
        await SqlAlchemyUserRepository(session).set_email_verified(
            user_id=user.id, verified=True
        )
        await _audit_account_event(
            session,
            user_id=user.id,
            action="account.email_verified",
            resource_type="user",
            resource_id=user.id,
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# 找回密码（防用户枚举）
# --------------------------------------------------------------------------- #


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(payload: PasswordResetRequest, request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    normalized_email = str(payload.email).strip().lower()
    # 防枚举主防线：对请求邮箱与客户端来源限流，遏制高频采样时序旁道。限流对
    # 存在/不存在的邮箱一视同仁（按提交值计数），不引入新的存在性信号；超限统一 429。
    limiter = request.app.state.auth_rate_limiter
    try:
        await limiter.ensure_allowed(scope="password_reset_ip", key=_client_key(request))
        await limiter.ensure_allowed(scope="password_reset", key=normalized_email)
    except RateLimitExceeded as error:
        raise _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limit_exceeded",
            "请求过于频繁，请稍后重试",
        ) from error

    raw_token, token_digest = request.app.state.session_token_manager.issue()
    async with request.app.state.session_factory() as session:
        user = await SqlAlchemyUserRepository(session).get_by_email(normalized_email)
        if user is not None:
            token = OneTimeToken.issue(
                user_id=user.id,
                purpose=AccountTokenPurpose.PASSWORD_RESET,
                token_digest=token_digest,
                ttl_seconds=settings.account_reset_token_ttl_seconds,
            )
            await SqlAlchemyAccountTokenRepository(session).add(
                token,
                dev_plaintext=raw_token if settings.expose_dev_account_tokens else None,
            )
            await _audit_account_event(
                session,
                user_id=user.id,
                action="account.password_reset_requested",
                resource_type="user",
                resource_id=user.id,
            )
            await session.commit()
        else:
            # 尽力而为的等价补偿（摘要计算 + 空提交）：仅缩小、不消除时序差异，
            # 不保证严格 constant-time；真正的时序旁道防护由上面的端点限流兜底。
            _equalize_reset_work(request)
            await session.commit()
    del raw_token
    # 无论账号是否存在都返回相同响应（逐字节恒等），防止以响应区分账号存在性。
    return {"status": "accepted"}


def _equalize_reset_work(request: Request) -> None:
    """账号不存在分支的尽力而为补偿：执行与真实签发同族的摘要计算。

    仅缩小耗时差异，不保证严格时序恒定；防枚举主防线是响应恒等 + 端点限流。
    """

    request.app.state.session_token_manager.digest(secrets.token_urlsafe(32))


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.get("/password-reset/dev-token", response_model=EmailVerificationTokenResponse)
async def read_dev_password_reset_token(
    request: Request,
    email: Annotated[EmailStr, Query()],
) -> EmailVerificationTokenResponse:
    settings = request.app.state.settings
    if not settings.expose_dev_account_tokens:
        raise _error(status.HTTP_404_NOT_FOUND, "resource_not_found", "资源不存在")
    async with request.app.state.session_factory() as session:
        user = await SqlAlchemyUserRepository(session).get_by_email(str(email))
        if user is None:
            raise _error(status.HTTP_404_NOT_FOUND, "resource_not_found", "资源不存在")
        plaintext = await SqlAlchemyAccountTokenRepository(
            session
        ).latest_dev_plaintext_for_user(
            user_id=user.id,
            purpose=AccountTokenPurpose.PASSWORD_RESET,
        )
    if plaintext is None:
        raise _error(status.HTTP_404_NOT_FOUND, "resource_not_found", "资源不存在")
    return EmailVerificationTokenResponse(token=plaintext)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    payload: PasswordResetConfirmRequest, request: Request
) -> Response:
    token_digest = request.app.state.session_token_manager.digest(payload.token)
    hasher = request.app.state.password_hasher
    async with request.app.state.session_factory() as session:
        tokens = SqlAlchemyAccountTokenRepository(session)
        record = await tokens.get_by_token_digest_for_update(
            purpose=AccountTokenPurpose.PASSWORD_RESET,
            token_digest=token_digest,
        )
        if record is None:
            raise _error(
                status.HTTP_400_BAD_REQUEST,
                "token_invalid_or_expired",
                "重置链接无效或已过期",
            )
        try:
            consumed = record.consume()
        except TokenInvalidOrExpired as error:
            raise _error(
                status.HTTP_400_BAD_REQUEST,
                "token_invalid_or_expired",
                "重置链接无效或已过期",
            ) from error
        await tokens.save(consumed)
        await SqlAlchemyUserRepository(session).set_password_hash(
            user_id=record.user_id,
            password_hash=hasher.hash(payload.new_password),
        )
        await SqlAlchemyAuthSessionRepository(session).revoke_all_for_user(
            user_id=record.user_id
        )
        await _audit_account_event(
            session,
            user_id=record.user_id,
            action="account.password_reset_completed",
            resource_type="user",
            resource_id=record.user_id,
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# 会话 / 设备管理
# --------------------------------------------------------------------------- #


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(request: Request) -> list[SessionResponse]:
    current_digest = _current_token_digest(request)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        records = await SqlAlchemyAuthSessionRepository(session).list_for_user(user.id)
    return [_session_response(record, current_digest) for record in records]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: UUID, request: Request) -> Response:
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        sessions = SqlAlchemyAuthSessionRepository(session)
        target = await sessions.get_for_user(user_id=user.id, session_id=session_id)
        if target is None:
            raise _error(status.HTTP_404_NOT_FOUND, "resource_not_found", "会话不存在")
        if target.is_active():
            await sessions.revoke(target)
        await _audit_account_event(
            session,
            user_id=user.id,
            action="account.session_revoked",
            resource_type="auth_session",
            resource_id=session_id,
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(request: Request) -> Response:
    current_digest = _current_token_digest(request)
    async with request.app.state.session_factory() as session:
        user = await authenticate_request(request, session)
        sessions = SqlAlchemyAuthSessionRepository(session)
        current_session_id = await _resolve_session_id(sessions, user.id, current_digest)
        revoked = await sessions.revoke_all_for_user(
            user_id=user.id,
            except_session_id=current_session_id,
        )
        await _audit_account_event(
            session,
            user_id=user.id,
            action="account.sessions_revoked_all",
            resource_type="user",
            resource_id=user.id,
            metadata={"revoked_count": revoked},
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _resolve_session_id(
    sessions: SqlAlchemyAuthSessionRepository,
    user_id: UUID,
    token_digest: str | None,
) -> UUID | None:
    if token_digest is None:
        return None
    for record in await sessions.list_for_user(user_id):
        if record.token_digest == token_digest:
            return record.id
    return None


def _session_response(record: AuthSession, current_digest: str | None) -> SessionResponse:
    return SessionResponse(
        id=record.id,
        created_at=record.created_at.isoformat(),
        expires_at=record.expires_at.isoformat(),
        revoked=record.revoked_at is not None,
        active=record.is_active(),
        current=current_digest is not None and record.token_digest == current_digest,
        user_agent=record.user_agent,
    )
