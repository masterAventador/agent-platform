from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class User:
    """平台本地用户身份。"""

    id: UUID
    email: str
    password_hash: str
    email_verified: bool
    created_at: datetime
    display_name: str | None = None

    @classmethod
    def create(
        cls,
        *,
        email: str,
        password_hash: str,
        display_name: str | None = None,
    ) -> "User":
        return cls(
            id=uuid4(),
            email=email.strip().lower(),
            password_hash=password_hash,
            email_verified=False,
            created_at=datetime.now(UTC),
            display_name=normalize_display_name(display_name),
        )


def normalize_display_name(display_name: str | None) -> str | None:
    if display_name is None:
        return None
    stripped = display_name.strip()
    return stripped or None
