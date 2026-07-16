from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Uuid,
    delete,
    desc,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.observability.correlation import current_correlation_id
from agent_platform.observability.metrics import (
    OperationalComponent,
    OperationalMetrics,
    active_operational_metrics,
)
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.tool_gateway.errors import ToolInvocationClaimRejected
from agent_platform.platform.tool_gateway.models import AuditEventType, ToolAuditEvent

_REDACTED = "[redacted]"
_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "body",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "input",
        "output",
        "password",
        "passwd",
        "payload",
        "private_key",
        "prompt",
        "raw_body",
        "refresh_token",
        "secret",
        "token",
    }
)
_SENSITIVE_INLINE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential)"
    r"\s*[:=]\s*[^\s,;&]+"
)


class ToolAuditPersistenceError(RuntimeError):
    """Sanitized error raised when an audit event cannot be persisted."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    tenant_id: UUID
    actor_user_id: UUID | None
    sequence: int
    action: str
    resource_type: str
    resource_id: UUID | None
    outcome: str
    occurred_at: datetime
    correlation_id: str | None
    previous_hash: str | None
    event_hash: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditIntegrityVerification:
    valid: bool
    checked_events: int
    first_invalid_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class AuditEventCreate:
    tenant_id: UUID
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID | None = None
    outcome: str = "succeeded"
    correlation_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("uq_audit_events_tenant_sequence", "tenant_id", "sequence", unique=True),
        Index("ix_audit_events_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_audit_events_tenant_action", "tenant_id", "action"),
        Index("ix_audit_events_tenant_resource", "tenant_id", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(96))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column("metadata", JSON)


class AuditChainStateRecord(Base):
    __tablename__ = "audit_chain_states"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    head_sequence: Mapped[int] = mapped_column(Integer)
    head_hash: Mapped[str] = mapped_column(String(64))
    retained_from_sequence: Mapped[int] = mapped_column(Integer)
    retention_previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def sanitize_audit_metadata(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Redact sensitive audit metadata at the persistence boundary."""

    return {
        key: _sanitize_audit_value(key=key, value=value)
        for key, value in metadata.items()
    }


def _sanitize_audit_value(*, key: str | None, value: JsonValue) -> JsonValue:
    if key is not None and _is_sensitive_metadata_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _sanitize_audit_string(value)
    if isinstance(value, list):
        return [_sanitize_audit_value(key=None, value=item) for item in value]
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_audit_value(
                key=str(child_key),
                value=child_value,
            )
            for child_key, child_value in value.items()
        }
    return value


def _sanitize_audit_string(value: str) -> str:
    return _SENSITIVE_INLINE_PATTERN.sub(
        lambda match: f"{match.group(1)}={_REDACTED}",
        value,
    )


def _is_sensitive_metadata_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if normalized in _SENSITIVE_METADATA_KEYS:
        return True
    return any(
        normalized.endswith(f"_{sensitive_key}")
        for sensitive_key in _SENSITIVE_METADATA_KEYS
    )


class ToolAuditRecord(Base):
    __tablename__ = "tool_audit_events"
    __table_args__ = (
        Index(
            "ix_tool_audit_events_invocation_id",
            "invocation_id",
            postgresql_where=text("invocation_id IS NOT NULL"),
            sqlite_where=text("invocation_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    employee_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tool_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    tool_name: Mapped[str] = mapped_column(String(128))
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    argument_keys: Mapped[list[str]] = mapped_column(JSON)
    argument_sha256: Mapped[str] = mapped_column(String(64))
    argument_size_bytes: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    invocation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )


class SqlAlchemyToolAuditSink:
    """Persist every audit event through its own transaction boundary."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def emit(self, event: ToolAuditEvent) -> None:
        try:
            async with self._session_factory() as session:
                if event.event_type is AuditEventType.STARTED:
                    await self._assert_started_claim_allowed(session, event)
                session.add(self._record(event))
                await session.commit()
        except ToolInvocationClaimRejected:
            raise
        except Exception:
            raise ToolAuditPersistenceError("Tool audit persistence failed") from None

    async def _assert_started_claim_allowed(
        self,
        session: AsyncSession,
        event: ToolAuditEvent,
    ) -> None:
        run = await SqlAlchemyRunRepository(session).get_for_update(
            tenant_id=event.tenant_id,
            run_id=event.run_id,
        )
        pending_cancel = await SqlAlchemyRunCommandRepository(session).unprocessed_cancel_commands(
            run_id=event.run_id
        )
        if (
            run is None
            or run.status
            in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }
            or pending_cancel
        ):
            raise ToolInvocationClaimRejected
        await self._after_started_claim_locked()

    async def _after_started_claim_locked(self) -> None:
        """Extension seam used by deterministic lock-order integration tests."""

    @staticmethod
    def _record(event: ToolAuditEvent) -> ToolAuditRecord:
        return ToolAuditRecord(
            id=uuid4(),
            event_type=event.event_type.value,
            occurred_at=event.occurred_at,
            tenant_id=event.tenant_id,
            run_id=event.run_id,
            employee_id=event.employee_id,
            user_id=event.user_id,
            tool_id=event.tool_id,
            tool_name=event.tool_name,
            risk=event.risk.value if event.risk is not None else None,
            argument_keys=list(event.argument_summary.keys),
            argument_sha256=event.argument_summary.sha256,
            argument_size_bytes=event.argument_summary.size_bytes,
            reason=event.reason,
            succeeded=event.succeeded,
            invocation_id=event.invocation_id,
        )


class SqlAlchemyToolAuditReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_started(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        invocation_id: UUID,
    ) -> bool:
        result = await self._session.execute(
            select(ToolAuditRecord.id)
            .where(
                ToolAuditRecord.invocation_id == invocation_id,
                ToolAuditRecord.tenant_id == tenant_id,
                ToolAuditRecord.run_id == run_id,
                ToolAuditRecord.event_type == AuditEventType.STARTED.value,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


class SqlAlchemyAuditEventRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self._session = session
        self._metrics = metrics

    async def add(self, event: AuditEventCreate) -> AuditEvent:
        started = perf_counter()
        try:
            created = await self._add(event)
        except Exception:
            self._record_metric("persist", "failed", started)
            raise
        self._record_metric("persist", "succeeded", started)
        return created

    async def _add(self, event: AuditEventCreate) -> AuditEvent:
        from agent_platform.infrastructure.database.repositories.tenants import TenantRecord

        await self._session.execute(
            select(TenantRecord.id)
            .where(TenantRecord.id == event.tenant_id)
            .with_for_update()
        )
        state = await self._chain_state_for_update(tenant_id=event.tenant_id)
        sequence = 1 if state is None else state.head_sequence + 1
        occurred_at = datetime.now(UTC)
        sanitized_metadata = sanitize_audit_metadata(event.metadata)
        previous_hash = None if state is None else state.head_hash
        correlation_id = event.correlation_id or current_correlation_id()
        event_id = uuid4()
        event_hash = _calculate_event_hash(
            event_id=event_id,
            tenant_id=event.tenant_id,
            actor_user_id=event.actor_user_id,
            sequence=sequence,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            previous_hash=previous_hash,
            metadata=sanitized_metadata,
        )
        record = AuditEventRecord(
            id=event_id,
            tenant_id=event.tenant_id,
            actor_user_id=event.actor_user_id,
            sequence=sequence,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            previous_hash=previous_hash,
            event_hash=event_hash,
            metadata_json=sanitized_metadata,
        )
        self._session.add(record)
        if state is None:
            self._session.add(
                AuditChainStateRecord(
                    tenant_id=event.tenant_id,
                    head_sequence=sequence,
                    head_hash=event_hash,
                    retained_from_sequence=1,
                    retention_previous_hash=None,
                    updated_at=occurred_at,
                )
            )
        else:
            state.head_sequence = sequence
            state.head_hash = event_hash
            state.updated_at = occurred_at
        await self._session.flush()
        return self._entity(record)

    async def list(
        self,
        *,
        tenant_id: UUID,
        limit: int,
        action: str | None = None,
        resource_type: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> list[AuditEvent]:
        statement = select(AuditEventRecord).where(AuditEventRecord.tenant_id == tenant_id)
        if action is not None:
            statement = statement.where(AuditEventRecord.action == action)
        if resource_type is not None:
            statement = statement.where(AuditEventRecord.resource_type == resource_type)
        if actor_user_id is not None:
            statement = statement.where(AuditEventRecord.actor_user_id == actor_user_id)
        result = await self._session.execute(
            statement.order_by(
                desc(AuditEventRecord.occurred_at),
                desc(AuditEventRecord.id),
            ).limit(limit)
        )
        return [self._entity(record) for record in result.scalars().all()]

    async def verify_integrity(
        self,
        *,
        tenant_id: UUID,
    ) -> AuditIntegrityVerification:
        started = perf_counter()
        try:
            verification = await self._verify_integrity(tenant_id=tenant_id)
        except Exception:
            self._record_metric("verify", "failed", started)
            raise
        self._record_metric("verify", "succeeded", started)
        return verification

    async def _verify_integrity(
        self,
        *,
        tenant_id: UUID,
    ) -> AuditIntegrityVerification:
        state = await self._session.get(AuditChainStateRecord, tenant_id)
        result = await self._session.execute(
            select(AuditEventRecord)
            .where(AuditEventRecord.tenant_id == tenant_id)
            .order_by(AuditEventRecord.sequence)
        )
        records = list(result.scalars().all())
        if state is None:
            return AuditIntegrityVerification(
                valid=not records,
                checked_events=0,
                first_invalid_sequence=records[0].sequence if records else None,
            )
        checked_events = 0
        previous_hash = state.retention_previous_hash
        expected_sequence = state.retained_from_sequence
        for record in records:
            if record.sequence != expected_sequence or record.previous_hash != previous_hash:
                return AuditIntegrityVerification(
                    valid=False,
                    checked_events=checked_events,
                    first_invalid_sequence=expected_sequence,
                )
            expected_hash = _calculate_event_hash(
                event_id=record.id,
                tenant_id=record.tenant_id,
                actor_user_id=record.actor_user_id,
                sequence=record.sequence,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                outcome=record.outcome,
                occurred_at=_ensure_aware(record.occurred_at),
                correlation_id=record.correlation_id,
                previous_hash=record.previous_hash,
                metadata=record.metadata_json,
            )
            if expected_hash != record.event_hash:
                return AuditIntegrityVerification(
                    valid=False,
                    checked_events=checked_events,
                    first_invalid_sequence=record.sequence,
                )
            checked_events += 1
            previous_hash = record.event_hash
            expected_sequence += 1
        if (
            expected_sequence != state.head_sequence + 1
            or previous_hash != state.head_hash
        ):
            return AuditIntegrityVerification(
                valid=False,
                checked_events=checked_events,
                first_invalid_sequence=expected_sequence,
            )
        return AuditIntegrityVerification(valid=True, checked_events=checked_events)

    async def purge_before(
        self,
        *,
        tenant_id: UUID,
        cutoff: datetime,
        limit: int,
    ) -> int:
        started = perf_counter()
        try:
            purged = await self._purge_before(tenant_id=tenant_id, cutoff=cutoff, limit=limit)
        except Exception:
            self._record_metric("retention", "failed", started)
            raise
        self._record_metric("retention", "succeeded", started)
        return purged

    async def _purge_before(
        self,
        *,
        tenant_id: UUID,
        cutoff: datetime,
        limit: int,
    ) -> int:
        if limit < 1 or limit > 10_000:
            raise ValueError("audit retention limit must be between 1 and 10000")
        state = await self._chain_state_for_update(tenant_id=tenant_id)
        if state is None:
            return 0
        result = await self._session.execute(
            select(AuditEventRecord)
            .where(
                AuditEventRecord.tenant_id == tenant_id,
                AuditEventRecord.occurred_at < cutoff,
            )
            .order_by(AuditEventRecord.sequence)
            .limit(limit)
            .with_for_update()
        )
        records = list(result.scalars().all())
        if not records:
            return 0
        if records[0].sequence != state.retained_from_sequence:
            raise RuntimeError("audit retention must remove a contiguous chain prefix")
        for previous, current in zip(records, records[1:], strict=False):
            if current.sequence != previous.sequence + 1:
                raise RuntimeError("audit retention encountered a sequence gap")
        last_purged = records[-1]
        await self._session.execute(
            delete(AuditEventRecord).where(
                AuditEventRecord.id.in_([record.id for record in records])
            )
        )
        state.retained_from_sequence = last_purged.sequence + 1
        state.retention_previous_hash = last_purged.event_hash
        state.updated_at = datetime.now(UTC)
        await self._session.flush()
        return len(records)

    def _record_metric(self, operation: str, outcome: str, started: float) -> None:
        metrics = self._metrics or active_operational_metrics()
        if metrics is None:
            return
        metrics.record(
            component=OperationalComponent.AUDIT,
            operation=operation,
            outcome=outcome,
            duration_ms=(perf_counter() - started) * 1000,
        )

    async def _chain_state_for_update(
        self, *, tenant_id: UUID
    ) -> AuditChainStateRecord | None:
        result = await self._session.execute(
            select(AuditChainStateRecord)
            .where(AuditChainStateRecord.tenant_id == tenant_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _entity(record: AuditEventRecord) -> AuditEvent:
        occurred_at = _ensure_aware(record.occurred_at)
        return AuditEvent(
            id=record.id,
            tenant_id=record.tenant_id,
            actor_user_id=record.actor_user_id,
            sequence=record.sequence,
            action=record.action,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            outcome=record.outcome,
            occurred_at=occurred_at,
            correlation_id=record.correlation_id,
            previous_hash=record.previous_hash,
            event_hash=record.event_hash,
            metadata=dict(record.metadata_json),
        )


async def purge_expired_audit_events(
    session: AsyncSession,
    *,
    cutoff: datetime,
    limit: int,
) -> int:
    """Purge expired audit events for every tenant while keeping each hash chain valid."""

    repository = SqlAlchemyAuditEventRepository(session)
    tenant_ids = (
        (await session.execute(select(AuditChainStateRecord.tenant_id))).scalars().all()
    )
    purged = 0
    for tenant_id in tenant_ids:
        purged += await repository.purge_before(
            tenant_id=tenant_id,
            cutoff=cutoff,
            limit=limit,
        )
    return purged


async def emit_audit_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    outcome: str = "succeeded",
    correlation_id: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> AuditEvent:
    return await SqlAlchemyAuditEventRepository(session).add(
        AuditEventCreate(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            correlation_id=correlation_id or current_correlation_id(),
            metadata=metadata or {},
        )
    )


def _calculate_event_hash(
    *,
    event_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID | None,
    sequence: int,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    outcome: str,
    occurred_at: datetime,
    correlation_id: str | None,
    previous_hash: str | None,
    metadata: Mapping[str, JsonValue],
) -> str:
    payload = {
        "id": str(event_id),
        "tenant_id": str(tenant_id),
        "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
        "sequence": sequence,
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id is not None else None,
        "outcome": outcome,
        "occurred_at": _ensure_aware(occurred_at).isoformat(),
        "correlation_id": correlation_id,
        "previous_hash": previous_hash,
        "metadata": metadata,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
