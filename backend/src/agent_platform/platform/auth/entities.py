from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class AuthSession:
    """可撤销的服务端登录会话。"""

    id: UUID
    user_id: UUID
    token_digest: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def issue(cls, *, user_id: UUID, token_digest: str, ttl_seconds: int) -> "AuthSession":
        created_at = datetime.now(UTC)
        return cls(
            id=uuid4(),
            user_id=user_id,
            token_digest=token_digest,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=ttl_seconds),
        )

    def is_active(self, *, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(UTC)
        return self.revoked_at is None and self.expires_at > current_time
