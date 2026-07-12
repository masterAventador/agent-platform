from dataclasses import dataclass

from agent_platform.platform.auth.entities import AuthSession
from agent_platform.platform.auth.errors import (
    AuthenticationRequired,
    InvalidCredentials,
    RegistrationUnavailable,
)
from agent_platform.platform.auth.ports import (
    AuthRateLimiter,
    AuthSessionRepository,
    PasswordHasher,
    SessionTokenManager,
    UserRepository,
)
from agent_platform.platform.users.entities import User


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    raw_token: str


class AuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: AuthSessionRepository,
        password_hasher: PasswordHasher,
        rate_limiter: AuthRateLimiter,
        token_manager: SessionTokenManager,
        session_ttl_seconds: int,
        require_email_verification: bool,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._password_hasher = password_hasher
        self._rate_limiter = rate_limiter
        self._token_manager = token_manager
        self._session_ttl_seconds = session_ttl_seconds
        self._require_email_verification = require_email_verification

    async def register(self, *, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        await self._rate_limiter.ensure_allowed(scope="register", key=normalized_email)
        if await self._users.get_by_email(normalized_email) is not None:
            raise RegistrationUnavailable

        user = User.create(
            email=normalized_email,
            password_hash=self._password_hasher.hash(password),
        )
        await self._users.add(user)
        return user

    async def login(self, *, email: str, password: str) -> IssuedSession:
        normalized_email = email.strip().lower()
        await self._rate_limiter.ensure_allowed(scope="login", key=normalized_email)
        user = await self._users.get_by_email(normalized_email)
        if user is None:
            self._password_hasher.verify_unknown(password)
            raise InvalidCredentials

        if not self._password_hasher.verify(password, user.password_hash):
            raise InvalidCredentials
        if self._require_email_verification and not user.email_verified:
            raise InvalidCredentials

        raw_token, token_digest = self._token_manager.issue()
        await self._sessions.add(
            AuthSession.issue(
                user_id=user.id,
                token_digest=token_digest,
                ttl_seconds=self._session_ttl_seconds,
            )
        )
        return IssuedSession(user=user, raw_token=raw_token)

    async def authenticate(self, raw_token: str | None) -> User:
        if raw_token is None:
            raise AuthenticationRequired
        token_digest = self._token_manager.digest(raw_token)
        session = await self._sessions.get_by_token_digest(token_digest)
        if session is None or not session.is_active():
            raise AuthenticationRequired
        user = await self._users.get_by_id(session.user_id)
        if user is None:
            raise AuthenticationRequired
        return user

    async def logout(self, raw_token: str | None) -> None:
        if raw_token is None:
            return
        session = await self._sessions.get_by_token_digest(self._token_manager.digest(raw_token))
        if session is not None and session.is_active():
            await self._sessions.revoke(session)
