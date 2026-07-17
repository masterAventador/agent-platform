"""账号一次性 token 领域实体（邮箱验证 / 找回密码）。

token 只存摘要（digest），明文只在签发时返回给受控通道，绝不落库。token 在
未消费且未过期时有效，消费后不可复用（防重放）。
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from agent_platform.platform.accounts.errors import TokenInvalidOrExpired


class AccountTokenPurpose(StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OneTimeToken:
    id: UUID
    user_id: UUID
    purpose: AccountTokenPurpose
    token_digest: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        *,
        user_id: UUID,
        purpose: AccountTokenPurpose,
        token_digest: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> "OneTimeToken":
        created_at = _now(now)
        return cls(
            id=uuid4(),
            user_id=user_id,
            purpose=purpose,
            token_digest=token_digest,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=ttl_seconds),
        )

    def is_valid(self, *, now: datetime | None = None) -> bool:
        return self.consumed_at is None and self.expires_at > _now(now)

    def consume(self, *, now: datetime | None = None) -> "OneTimeToken":
        current = _now(now)
        if not self.is_valid(now=current):
            raise TokenInvalidOrExpired
        return replace(self, consumed_at=current)
