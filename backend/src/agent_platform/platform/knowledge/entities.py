from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    provider: str
    provider_id: str
    created_by: UUID
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        description: str,
        provider: str,
        provider_id: str,
        created_by: UUID,
    ) -> "KnowledgeBase":
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name.strip(),
            description=description.strip(),
            provider=provider,
            provider_id=provider_id,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
