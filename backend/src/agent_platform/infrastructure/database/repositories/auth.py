from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid, func, select, update
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
            )
        )
        try:
            await self._session.commit()
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
            )
        )
        await self._session.commit()

    async def get_by_token_digest(self, token_digest: str) -> AuthSession | None:
        result = await self._session.execute(
            select(AuthSessionRecord).where(AuthSessionRecord.token_digest == token_digest)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return AuthSession(
            id=record.id,
            user_id=record.user_id,
            token_digest=record.token_digest,
            created_at=self._as_utc(record.created_at),
            expires_at=self._as_utc(record.expires_at),
            revoked_at=self._as_utc(record.revoked_at) if record.revoked_at else None,
        )

    async def revoke(self, session: AuthSession) -> None:
        await self._session.execute(
            update(AuthSessionRecord)
            .where(AuthSessionRecord.id == session.id)
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
