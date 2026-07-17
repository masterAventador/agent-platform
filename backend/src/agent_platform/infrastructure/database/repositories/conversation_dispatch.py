"""会话轮次 Run 的共享创建路径。

API 追加消息 / 失败重试与 Worker 终态结算后的自动续跑派生复用同一实现，
避免为派生另建旁路：上下文裁剪、输入构造、Run/附件/START 命令的持久化
只存在这一份语义。
"""

from collections.abc import Sequence
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationMessageRepository,
)
from agent_platform.infrastructure.database.repositories.run_dispatch import (
    create_employee_run,
)
from agent_platform.platform.artifacts.entities import File
from agent_platform.platform.conversations.entities import (
    CONVERSATION_CONTEXT_MAX_CHARS,
    CONVERSATION_CONTEXT_MAX_MESSAGES,
    Conversation,
    ConversationMessage,
)
from agent_platform.platform.runs.entities import Run


def conversation_context_payload(
    messages: Sequence[ConversationMessage],
) -> dict[str, JsonValue]:
    return {
        "messages": [
            {
                "role": message.role.value,
                "content": message.content,
                "attachment_ids": [str(value) for value in message.attachment_ids],
            }
            for message in messages
        ],
        "max_messages": CONVERSATION_CONTEXT_MAX_MESSAGES,
        "max_chars": CONVERSATION_CONTEXT_MAX_CHARS,
    }


async def build_conversation_run_input(
    *,
    messages: SqlAlchemyConversationMessageRepository,
    conversation: Conversation,
    content: str | None,
    retry_of_run_id: UUID | None = None,
) -> dict[str, JsonValue]:
    context = await messages.list_recent_context(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        max_messages=CONVERSATION_CONTEXT_MAX_MESSAGES,
        max_chars=CONVERSATION_CONTEXT_MAX_CHARS,
    )
    payload: dict[str, JsonValue] = {
        "conversation_id": str(conversation.id),
        "message": content or (context[-1].content if context else ""),
        "conversation_context": conversation_context_payload(context),
    }
    if retry_of_run_id is not None:
        payload["retry_of_run_id"] = str(retry_of_run_id)
    return payload


async def create_conversation_run(
    *,
    database_session: AsyncSession,
    conversation: Conversation,
    employee_version: int,
    created_by: UUID,
    input_data: dict[str, JsonValue],
    attachment_files: Sequence[File],
    run_id: UUID | None = None,
) -> Run:
    return await create_employee_run(
        database_session=database_session,
        tenant_id=conversation.tenant_id,
        employee_id=conversation.employee_id,
        employee_version=employee_version,
        created_by=created_by,
        input_data=input_data,
        attachment_files=attachment_files,
        conversation_id=conversation.id,
        thread_id=conversation.thread_id,
        run_id=run_id,
    )
