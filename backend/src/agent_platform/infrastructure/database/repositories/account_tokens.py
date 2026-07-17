"""账号一次性 token 仓储（邮箱验证 / 找回密码）。

只存 token 摘要。消费必须先取行锁再做领域校验并落库，防止并发重放。
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.accounts.tokens import (
    AccountTokenPurpose,
    OneTimeToken,
)


class AccountTokenRecord(Base):
    __tablename__ = "account_tokens"
    __table_args__ = (
        # 与迁移 0034 对齐：唯一索引显式命名 uq_...（而非列级 index=True 自动名 ix_...），
        # 避免 autogenerate 漂移守卫误判需增删索引。
        Index("uq_account_tokens_token_digest", "token_digest", unique=True),
        Index("ix_account_tokens_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(32))
    token_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dev_plaintext: Mapped[str | None] = mapped_column(String(256), nullable=True)


class SqlAlchemyAccountTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: OneTimeToken, *, dev_plaintext: str | None = None) -> None:
        self._session.add(
            AccountTokenRecord(
                id=token.id,
                user_id=token.user_id,
                purpose=token.purpose.value,
                token_digest=token.token_digest,
                created_at=token.created_at,
                expires_at=token.expires_at,
                consumed_at=token.consumed_at,
                dev_plaintext=dev_plaintext,
            )
        )
        await self._session.flush()

    async def latest_dev_plaintext_for_user(
        self,
        *,
        user_id: UUID,
        purpose: AccountTokenPurpose,
    ) -> str | None:
        result = await self._session.execute(
            select(AccountTokenRecord)
            .where(
                AccountTokenRecord.user_id == user_id,
                AccountTokenRecord.purpose == purpose.value,
                AccountTokenRecord.consumed_at.is_(None),
                AccountTokenRecord.dev_plaintext.is_not(None),
            )
            .order_by(AccountTokenRecord.created_at.desc())
        )
        record = result.scalars().first()
        return record.dev_plaintext if record is not None else None

    async def get_by_token_digest_for_update(
        self, *, purpose: AccountTokenPurpose, token_digest: str
    ) -> OneTimeToken | None:
        result = await self._session.execute(
            select(AccountTokenRecord)
            .where(
                AccountTokenRecord.token_digest == token_digest,
                AccountTokenRecord.purpose == purpose.value,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        return _to_entity(record) if record is not None else None

    async def save(self, token: OneTimeToken) -> None:
        record = await self._session.get(AccountTokenRecord, token.id)
        if record is None:
            raise RuntimeError("account token record disappeared before save")
        record.consumed_at = token.consumed_at
        await self._session.flush()


def _to_entity(record: AccountTokenRecord) -> OneTimeToken:
    return OneTimeToken(
        id=record.id,
        user_id=record.user_id,
        purpose=AccountTokenPurpose(record.purpose),
        token_digest=record.token_digest,
        created_at=_as_utc(record.created_at),
        expires_at=_as_utc(record.expires_at),
        consumed_at=_as_utc(record.consumed_at) if record.consumed_at else None,
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
