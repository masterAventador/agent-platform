from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4, uuid5

MAX_CONVERSATION_MESSAGE_CONTENT_CHARS = 12_000
CONVERSATION_MESSAGE_TRUNCATED_MARKER = "内容已截断"
# 会话轮次输入的上下文裁剪边界：API 追加消息与 Worker 自动续跑派生共用单一来源。
CONVERSATION_CONTEXT_MAX_MESSAGES = 8
CONVERSATION_CONTEXT_MAX_CHARS = MAX_CONVERSATION_MESSAGE_CONTENT_CHARS
# 自动续跑派生 Run 的确定性幂等键命名空间（固定值，不得变更，否则破坏崩溃恢复幂等）。
CONVERSATION_FOLLOWUP_RUN_NAMESPACE = UUID("6f2f8f0a-9d1c-4a5b-8e5e-3a4f6c05c051")


def conversation_followup_run_id(*, conversation_id: UUID, trigger_message_id: UUID) -> UUID:
    """由 (conversation_id, 触发消息) 派生确定性 Run id，保证崩溃恢复时不重复派生。"""
    return uuid5(
        CONVERSATION_FOLLOWUP_RUN_NAMESPACE,
        f"{conversation_id}:{trigger_message_id}",
    )


def limit_conversation_message_content(content: str) -> str:
    if len(content) <= MAX_CONVERSATION_MESSAGE_CONTENT_CHARS:
        return content
    digest = sha256(content.encode("utf-8")).hexdigest()[:12]
    suffix = (
        f"\n\n[{CONVERSATION_MESSAGE_TRUNCATED_MARKER}，完整输出保留在任务事件中；"
        f"sha256:{digest}]"
    )
    prefix_chars = MAX_CONVERSATION_MESSAGE_CONTENT_CHARS - len(suffix)
    if prefix_chars <= 0:
        return content[:MAX_CONVERSATION_MESSAGE_CONTENT_CHARS]
    return f"{content[:prefix_chars]}{suffix}"


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
            content=limit_conversation_message_content(content),
            run_id=run_id,
            attachment_ids=attachment_ids,
            created_at=datetime.now(UTC),
        )
