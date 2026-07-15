from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.runs import (
    RunRecord,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.conversations.entities import (
    MAX_CONVERSATION_MESSAGE_CONTENT_CHARS,
    Conversation,
    ConversationMessage,
    ConversationMessageRole,
)
from agent_platform.platform.runs.entities import Run, RunStatus


class ConversationRecord(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("employees.id"), index=True
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    thread_id: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id_id"),
    )


class ConversationMessageRecord(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(String(MAX_CONVERSATION_MESSAGE_CONTENT_CHARS))
    attachment_ids: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "sequence",
            name="uq_conversation_messages_sequence",
        ),
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> None:
        self._session.add(
            ConversationRecord(
                id=conversation.id,
                tenant_id=conversation.tenant_id,
                employee_id=conversation.employee_id,
                created_by=conversation.created_by,
                title=conversation.title,
                thread_id=conversation.thread_id,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                last_message_at=conversation.last_message_at,
            )
        )
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, conversation_id: UUID) -> Conversation | None:
        record = (
            await self._session.execute(
                select(ConversationRecord).where(
                    ConversationRecord.tenant_id == tenant_id,
                    ConversationRecord.id == conversation_id,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def update(self, conversation: Conversation) -> None:
        record = (
            await self._session.execute(
                select(ConversationRecord).where(
                    ConversationRecord.tenant_id == conversation.tenant_id,
                    ConversationRecord.id == conversation.id,
                )
            )
        ).scalar_one()
        record.title = conversation.title
        record.updated_at = conversation.updated_at
        record.last_message_at = conversation.last_message_at
        await self._session.flush()

    async def list(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID | None,
        limit: int = 100,
    ) -> list[Conversation]:
        query = select(ConversationRecord).where(ConversationRecord.tenant_id == tenant_id)
        if created_by is not None:
            query = query.where(ConversationRecord.created_by == created_by)
        result = await self._session.execute(
            query.order_by(
                ConversationRecord.last_message_at.desc().nullslast(),
                ConversationRecord.updated_at.desc(),
            ).limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def list_runs(self, *, tenant_id: UUID, conversation_id: UUID) -> Sequence[Run]:
        result = await self._session.execute(
            select(RunRecord)
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.conversation_id == conversation_id,
            )
            .order_by(RunRecord.created_at)
        )
        return [SqlAlchemyRunRepository._to_entity(record) for record in result.scalars()]

    async def latest_active_run(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> Run | None:
        result = await self._session.execute(
            select(RunRecord)
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_FOR_INPUT.value,
                        RunStatus.WAITING_FOR_APPROVAL.value,
                    ]
                ),
            )
            .order_by(RunRecord.created_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        return SqlAlchemyRunRepository._to_entity(record) if record is not None else None

    async def latest_failed_run(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        run_id: UUID | None = None,
    ) -> Run | None:
        query = select(RunRecord).where(
            RunRecord.tenant_id == tenant_id,
            RunRecord.conversation_id == conversation_id,
            RunRecord.status == RunStatus.FAILED.value,
        )
        if run_id is not None:
            query = query.where(RunRecord.id == run_id)
        result = await self._session.execute(query.order_by(RunRecord.created_at.desc()).limit(1))
        record = result.scalar_one_or_none()
        return SqlAlchemyRunRepository._to_entity(record) if record is not None else None

    @staticmethod
    def _to_entity(record: ConversationRecord) -> Conversation:
        return Conversation(
            id=record.id,
            tenant_id=record.tenant_id,
            employee_id=record.employee_id,
            created_by=record.created_by,
            title=record.title,
            thread_id=record.thread_id,
            created_at=_as_utc(record.created_at),
            updated_at=_as_utc(record.updated_at),
            last_message_at=_as_utc(record.last_message_at) if record.last_message_at else None,
        )


class SqlAlchemyConversationMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: ConversationMessage) -> None:
        self._session.add(
            ConversationMessageRecord(
                id=message.id,
                tenant_id=message.tenant_id,
                conversation_id=message.conversation_id,
                run_id=message.run_id,
                sequence=message.sequence,
                role=message.role.value,
                content=message.content,
                attachment_ids=[str(value) for value in message.attachment_ids],
                created_at=message.created_at,
            )
        )
        await self._session.flush()

    async def next_sequence(self, *, tenant_id: UUID, conversation_id: UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(ConversationMessageRecord.sequence), 0)).where(
                ConversationMessageRecord.tenant_id == tenant_id,
                ConversationMessageRecord.conversation_id == conversation_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def list(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        limit: int = 200,
    ) -> Sequence[ConversationMessage]:
        result = await self._session.execute(
            select(ConversationMessageRecord)
            .where(
                ConversationMessageRecord.tenant_id == tenant_id,
                ConversationMessageRecord.conversation_id == conversation_id,
            )
            .order_by(ConversationMessageRecord.sequence)
            .limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def list_recent_context(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        max_messages: int,
        max_chars: int,
    ) -> Sequence[ConversationMessage]:
        result = await self._session.execute(
            select(ConversationMessageRecord)
            .where(
                ConversationMessageRecord.tenant_id == tenant_id,
                ConversationMessageRecord.conversation_id == conversation_id,
                ConversationMessageRecord.role.in_(
                    [
                        ConversationMessageRole.USER.value,
                        ConversationMessageRole.ASSISTANT.value,
                    ]
                ),
            )
            .order_by(ConversationMessageRecord.sequence.desc())
            .limit(max_messages * 2)
        )
        selected: list[ConversationMessage] = []
        total = 0
        for record in result.scalars():
            entity = self._to_entity(record)
            total += len(entity.content)
            if selected and total > max_chars:
                break
            selected.append(entity)
            if len(selected) >= max_messages:
                break
        return list(reversed(selected))

    async def bind_run(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
    ) -> None:
        record = (
            await self._session.execute(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.tenant_id == tenant_id,
                    ConversationMessageRecord.conversation_id == conversation_id,
                    ConversationMessageRecord.id == message_id,
                )
            )
        ).scalar_one()
        record.run_id = run_id
        await self._session.flush()

    async def exists_for_run_event(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        role: ConversationMessageRole,
        content: str,
    ) -> bool:
        result = await self._session.execute(
            select(ConversationMessageRecord.id)
            .where(
                ConversationMessageRecord.tenant_id == tenant_id,
                ConversationMessageRecord.conversation_id == conversation_id,
                ConversationMessageRecord.run_id == run_id,
                ConversationMessageRecord.role == role.value,
                ConversationMessageRecord.content == content,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _to_entity(record: ConversationMessageRecord) -> ConversationMessage:
        return ConversationMessage(
            id=record.id,
            tenant_id=record.tenant_id,
            conversation_id=record.conversation_id,
            run_id=record.run_id,
            sequence=record.sequence,
            role=ConversationMessageRole(record.role),
            content=record.content,
            attachment_ids=tuple(UUID(value) for value in record.attachment_ids),
            created_at=_as_utc(record.created_at),
        )
