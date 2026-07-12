from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Tenant:
    """平台租户。"""

    id: UUID
    name: str
    slug: str
    created_at: datetime

    @classmethod
    def create(cls, *, name: str, slug: str) -> "Tenant":
        return cls(
            id=uuid4(),
            name=name.strip(),
            slug=slug.strip().lower(),
            created_at=datetime.now(UTC),
        )
