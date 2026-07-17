from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CursorResult,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.auth.entities import AuthSession
from agent_platform.platform.auth.errors import RegistrationUnavailable
from agent_platform.platform.users.entities import User


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(512))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(
            UserRecord(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                email_verified=user.email_verified,
                created_at=user.created_at,
                display_name=user.display_name,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise RegistrationUnavailable from error

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(UserRecord).where(UserRecord.email == email.strip().lower())
        )
        return self._to_entity(result.scalar_one_or_none())

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._to_entity(await self._session.get(UserRecord, user_id))

    async def update_profile(self, *, user_id: UUID, display_name: str | None) -> None:
        await self._session.execute(
            update(UserRecord)
            .where(UserRecord.id == user_id)
            .values(display_name=display_name)
        )
        await self._session.flush()

    async def set_password_hash(self, *, user_id: UUID, password_hash: str) -> None:
        await self._session.execute(
            update(UserRecord)
            .where(UserRecord.id == user_id)
            .values(password_hash=password_hash)
        )
        await self._session.flush()

    async def set_email_verified(self, *, user_id: UUID, verified: bool) -> None:
        await self._session.execute(
            update(UserRecord)
            .where(UserRecord.id == user_id)
            .values(email_verified=verified)
        )
        await self._session.flush()

    @staticmethod
    def _to_entity(record: UserRecord | None) -> User | None:
        if record is None:
            return None
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return User(
            id=record.id,
            email=record.email,
            password_hash=record.password_hash,
            email_verified=record.email_verified,
            created_at=created_at,
            display_name=record.display_name,
        )


class SqlAlchemyAuthSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: AuthSession) -> None:
        self._session.add(
            AuthSessionRecord(
                id=session.id,
                user_id=session.user_id,
                token_digest=session.token_digest,
                created_at=session.created_at,
                expires_at=session.expires_at,
                revoked_at=session.revoked_at,
                user_agent=session.user_agent,
            )
        )
        await self._session.flush()

    async def get_by_token_digest(self, token_digest: str) -> AuthSession | None:
        result = await self._session.execute(
            select(AuthSessionRecord).where(AuthSessionRecord.token_digest == token_digest)
        )
        return self._to_entity(result.scalar_one_or_none())

    async def list_for_user(self, user_id: UUID) -> list[AuthSession]:
        result = await self._session.execute(
            select(AuthSessionRecord)
            .where(AuthSessionRecord.user_id == user_id)
            .order_by(AuthSessionRecord.created_at.desc())
        )
        return [self._entity(record) for record in result.scalars().all()]

    async def get_for_user(self, *, user_id: UUID, session_id: UUID) -> AuthSession | None:
        record = await self._session.get(AuthSessionRecord, session_id)
        if record is None or record.user_id != user_id:
            return None
        return self._to_entity(record)

    async def revoke(self, session: AuthSession) -> None:
        await self._session.execute(
            update(AuthSessionRecord)
            .where(AuthSessionRecord.id == session.id)
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.flush()

    async def revoke_all_for_user(
        self,
        *,
        user_id: UUID,
        except_session_id: UUID | None = None,
    ) -> int:
        statement = (
            update(AuthSessionRecord)
            .where(
                AuthSessionRecord.user_id == user_id,
                AuthSessionRecord.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        if except_session_id is not None:
            statement = statement.where(AuthSessionRecord.id != except_session_id)
        result = cast(CursorResult[Any], await self._session.execute(statement))
        await self._session.flush()
        return result.rowcount or 0

    @staticmethod
    def _to_entity(record: AuthSessionRecord | None) -> AuthSession | None:
        if record is None:
            return None
        return SqlAlchemyAuthSessionRepository._entity(record)

    @staticmethod
    def _entity(record: AuthSessionRecord) -> AuthSession:
        return AuthSession(
            id=record.id,
            user_id=record.user_id,
            token_digest=record.token_digest,
            created_at=SqlAlchemyAuthSessionRepository._as_utc(record.created_at),
            expires_at=SqlAlchemyAuthSessionRepository._as_utc(record.expires_at),
            revoked_at=(
                SqlAlchemyAuthSessionRepository._as_utc(record.revoked_at)
                if record.revoked_at
                else None
            ),
            user_agent=record.user_agent,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
