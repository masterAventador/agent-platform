from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class ConversationMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    created_by: UUID
    title: str
    thread_id: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        employee_id: UUID,
        created_by: UUID,
        title: str | None,
    ) -> "Conversation":
        conversation_id = uuid4()
        now = datetime.now(UTC)
        return cls(
            id=conversation_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            created_by=created_by,
            title=(title or "新的会话").strip() or "新的会话",
            thread_id=f"conversation:{conversation_id}",
            created_at=now,
            updated_at=now,
        )

    def touch(self, at: datetime | None = None) -> "Conversation":
        now = at or datetime.now(UTC)
        return replace(self, updated_at=now, last_message_at=now)


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    sequence: int
    role: ConversationMessageRole
    content: str
    created_at: datetime
    run_id: UUID | None = None
    attachment_ids: tuple[UUID, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        sequence: int,
        role: ConversationMessageRole,
        content: str,
        run_id: UUID | None = None,
        attachment_ids: tuple[UUID, ...] = (),
    ) -> "ConversationMessage":
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            sequence=sequence,
            role=role,
            content=content,
            run_id=run_id,
            attachment_ids=attachment_ids,
            created_at=datetime.now(UTC),
        )
