import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.dead_letters import (
    RunDeadLetterRecord,
    SqlAlchemyRunDeadLetterRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnership,
    RuntimeOwnershipBusy,
    SqlAlchemyRuntimeOwnershipRepository,
)
from agent_platform.infrastructure.queue.redis_streams import RunQueueDelivery
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus

DEAD_LETTER_ERROR_CODE = "delivery_attempts_exhausted"
DELIVERY_PROCESSING_ERROR_TYPE = "delivery_processing_failed"
MALFORMED_MESSAGE_ERROR_TYPE = "malformed_queue_message"
_ALLOWED_ERROR_TYPES = frozenset({DELIVERY_PROCESSING_ERROR_TYPE, MALFORMED_MESSAGE_ERROR_TYPE})
_MAX_RAW_FIELDS = 32
_KNOWN_QUEUE_FIELDS = frozenset({"command_id", "run_id", "tenant_id", "action", "payload"})


@dataclass(frozen=True, slots=True)
class RunDeadLetter:
    id: UUID
    source_stream: str
    original_delivery_id: str
    original_command_id: UUID | None
    original_run_id: UUID | None
    tenant_id: UUID | None
    action: str | None
    attempts: int
    error_type: str
    is_malformed: bool
    raw_fields_summary: dict[str, JsonValue]
    failed_at: datetime
    replayed_run_id: UUID | None
    replayed_command_id: UUID | None
    replayed_at: datetime | None
    settled_run_id: UUID | None
    mirrored_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReplayedRun:
    run_id: UUID
    command_id: UUID


class DeadLetterSettlementPending(RuntimeError):
    """死信已耐久记录，但目标 run 当前不能安全结算，原 delivery 不得 ACK。"""

    def __init__(self, dead_letter: RunDeadLetter) -> None:
        super().__init__("dead_letter_settlement_pending")
        self.dead_letter = dead_letter


class DeadLetterNotReplayable(ValueError):
    """死信不具备安全重放所需的合法来源信息。"""


class DeadLetterNotSettled(ValueError):
    """死信尚未完成业务结算，不能创建重放任务。"""


class DeadLetterMirrorPublisher(Protocol):
    async def publish_dead_letter(self, record: RunDeadLetter) -> None: ...


class RunDeadLetterService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._delivery_locks = tuple(asyncio.Lock() for _ in range(64))

    async def record_failure(
        self,
        delivery: RunQueueDelivery,
        *,
        attempts: int,
        error_type: str,
        ownership: RuntimeOwnership | None = None,
    ) -> RunDeadLetter:
        lock = self._delivery_locks[hash(delivery.delivery_id) % len(self._delivery_locks)]
        async with lock:
            return await self._record_failure(
                delivery,
                attempts=attempts,
                error_type=error_type,
                ownership=ownership,
            )

    async def _record_failure(
        self,
        delivery: RunQueueDelivery,
        *,
        attempts: int,
        error_type: str,
        ownership: RuntimeOwnership | None,
    ) -> RunDeadLetter:
        self._validate_error_type(error_type)
        message = delivery.message
        async with self._session_factory() as session:
            repository = SqlAlchemyRunDeadLetterRepository(session)
            commands = SqlAlchemyRunCommandRepository(session)
            command = await commands.get(message.command_id)
            runs = SqlAlchemyRunRepository(session)
            run = await runs.get(
                tenant_id=message.tenant_id,
                run_id=message.run_id,
            )
            self._validate_delivery_binding(command=command, run=run, delivery=delivery)
            existing = await repository.get_by_delivery_id(
                source_stream=delivery.source_stream,
                delivery_id=delivery.delivery_id,
            )
            if existing is not None:
                record = existing
                self._validate_record_binding(record=record, delivery=delivery)
            else:
                record = RunDeadLetterRecord(
                    id=uuid4(),
                    source_stream=delivery.source_stream,
                    original_delivery_id=delivery.delivery_id,
                    original_command_id=message.command_id,
                    original_run_id=message.run_id,
                    tenant_id=message.tenant_id,
                    action=message.action,
                    attempts=attempts,
                    error_type=error_type,
                    is_malformed=False,
                    raw_fields_summary={},
                    failed_at=datetime.now(UTC),
                    replayed_run_id=None,
                    replayed_command_id=None,
                    replayed_at=None,
                    settled_run_id=None,
                    mirrored_at=None,
                )
                try:
                    await repository.add(record)
                except IntegrityError as error:
                    await session.rollback()
                    record = await self._existing_record_after_integrity(
                        delivery.delivery_id,
                        delivery.source_stream,
                        error,
                    )
            await session.commit()
        return await self._settle_valid(record.id, delivery, ownership=ownership)

    async def record_malformed(
        self,
        *,
        delivery_id: str,
        attempts: int,
        error_type: str,
        raw_fields: dict[str, str],
        source_stream: str = "agent-platform:runs",
        ownerships: tuple[RuntimeOwnership, ...] = (),
    ) -> RunDeadLetter:
        lock = self._delivery_locks[hash(delivery_id) % len(self._delivery_locks)]
        async with lock:
            return await self._record_malformed(
                delivery_id=delivery_id,
                attempts=attempts,
                error_type=error_type,
                raw_fields=raw_fields,
                source_stream=source_stream,
                ownerships=ownerships,
            )

    async def _record_malformed(
        self,
        *,
        delivery_id: str,
        attempts: int,
        error_type: str,
        raw_fields: dict[str, str],
        source_stream: str,
        ownerships: tuple[RuntimeOwnership, ...],
    ) -> RunDeadLetter:
        self._validate_error_type(error_type)
        summary = self._summarize_fields(raw_fields)
        candidate_command_id = self._safe_uuid(raw_fields.get("command_id"))
        candidate_run_id = self._safe_uuid(raw_fields.get("run_id"))
        candidate_tenant_id = self._safe_uuid(raw_fields.get("tenant_id"))
        async with self._session_factory() as session:
            repository = SqlAlchemyRunDeadLetterRepository(session)
            existing = await repository.get_by_delivery_id(
                source_stream=source_stream,
                delivery_id=delivery_id,
            )
            if existing is not None:
                record = existing
                if record.settled_run_id is not None:
                    return self._to_entity(record)
            else:
                record = RunDeadLetterRecord(
                    id=uuid4(),
                    source_stream=source_stream,
                    original_delivery_id=delivery_id,
                    original_command_id=candidate_command_id,
                    original_run_id=candidate_run_id,
                    tenant_id=None,
                    action=None,
                    attempts=attempts,
                    error_type=error_type,
                    is_malformed=True,
                    raw_fields_summary=summary,
                    failed_at=datetime.now(UTC),
                    replayed_run_id=None,
                    replayed_command_id=None,
                    replayed_at=None,
                    settled_run_id=None,
                    mirrored_at=None,
                )
                try:
                    await repository.add(record)
                except IntegrityError as error:
                    await session.rollback()
                    existing_after_conflict = await repository.get_by_delivery_id(
                        source_stream=source_stream,
                        delivery_id=delivery_id,
                    )
                    if existing_after_conflict is None:
                        raise error
                    record = existing_after_conflict
            verification_command_id: UUID | None
            verification_run_id: UUID | None
            verification_tenant_id: UUID | None
            if record.tenant_id is not None:
                verification_command_id = record.original_command_id
                verification_run_id = record.original_run_id
                verification_tenant_id = record.tenant_id
            else:
                verification_command_id = candidate_command_id
                verification_run_id = candidate_run_id
                verification_tenant_id = candidate_tenant_id
            verified_target = False
            if (
                verification_command_id is not None
                and verification_run_id is not None
                and verification_tenant_id is not None
                and record.original_command_id == verification_command_id
                and record.original_run_id == verification_run_id
            ):
                commands = SqlAlchemyRunCommandRepository(session)
                command = await commands.get(verification_command_id)
                runs = SqlAlchemyRunRepository(session)
                run = await runs.get(
                    tenant_id=verification_tenant_id,
                    run_id=verification_run_id,
                )
                if (
                    command is not None
                    and run is not None
                    and command.run_id == run.id
                    and command.tenant_id == run.tenant_id
                ):
                    verified_target = True
                    record.tenant_id = run.tenant_id
                    ownership_repository = SqlAlchemyRuntimeOwnershipRepository(session)
                    settlement_ownership = next(
                        (
                            ownership
                            for ownership in ownerships
                            if ownership.run_id == run.id and ownership.tenant_id == run.tenant_id
                        ),
                        None,
                    )
                    if settlement_ownership is not None:
                        await ownership_repository.assert_owned(
                            run_id=run.id,
                            owner_id=settlement_ownership.owner_id or "",
                            epoch=settlement_ownership.epoch,
                            now=datetime.now(UTC),
                        )
                    if settlement_ownership is None:
                        try:
                            settlement_ownership = await ownership_repository.claim(
                                run_id=run.id,
                                tenant_id=run.tenant_id,
                                owner_id=f"dead-letter:{record.id}",
                                now=datetime.now(UTC),
                                lease_duration=timedelta(seconds=30),
                            )
                        except RuntimeOwnershipBusy:
                            settlement_ownership = None
                    if settlement_ownership is not None:
                        locked_run = await runs.get_for_update(
                            tenant_id=run.tenant_id,
                            run_id=run.id,
                        )
                        if locked_run is None:
                            raise LookupError(run.id)
                        run = locked_run
                        if run.status not in {
                            RunStatus.COMPLETED,
                            RunStatus.FAILED,
                            RunStatus.CANCELLED,
                        }:
                            await runs.update(
                                run.transition_to(
                                    RunStatus.FAILED,
                                    error_code=DEAD_LETTER_ERROR_CODE,
                                    error_message=None,
                                )
                            )
                        await commands.mark_processed(command.id)
                        await ownership_repository.release(
                            run_id=run.id,
                            owner_id=settlement_ownership.owner_id or "",
                            epoch=settlement_ownership.epoch,
                        )
                        record.settled_run_id = run.id
            if not verified_target:
                record.tenant_id = None
            await session.commit()
            dead_letter = self._to_entity(record)
            if verified_target and record.settled_run_id is None:
                raise DeadLetterSettlementPending(dead_letter)
            return dead_letter

    async def list(self, *, tenant_id: UUID, limit: int = 100) -> list[RunDeadLetter]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        async with self._session_factory() as session:
            records = await SqlAlchemyRunDeadLetterRepository(session).list(
                tenant_id=tenant_id,
                limit=limit,
            )
            return [self._to_entity(record) for record in records]

    async def _settle_valid(
        self,
        dead_letter_id: UUID,
        delivery: RunQueueDelivery,
        *,
        ownership: RuntimeOwnership | None,
    ) -> RunDeadLetter:
        message = delivery.message
        async with self._session_factory() as session:
            repository = SqlAlchemyRunDeadLetterRepository(session)
            record = await repository.get_by_delivery_id(
                source_stream=delivery.source_stream,
                delivery_id=delivery.delivery_id,
            )
            if record is None or record.id != dead_letter_id:
                raise LookupError(dead_letter_id)
            self._validate_record_binding(record=record, delivery=delivery)
            if record.settled_run_id is not None:
                return self._to_entity(record)
            commands = SqlAlchemyRunCommandRepository(session)
            command = await commands.get(message.command_id)
            runs = SqlAlchemyRunRepository(session)
            run = await runs.get(
                tenant_id=message.tenant_id,
                run_id=message.run_id,
            )
            self._validate_delivery_binding(command=command, run=run, delivery=delivery)
            ownership_repository = SqlAlchemyRuntimeOwnershipRepository(session)
            settlement_ownership = ownership
            if settlement_ownership is not None:
                await ownership_repository.assert_owned(
                    run_id=message.run_id,
                    owner_id=settlement_ownership.owner_id or "",
                    epoch=settlement_ownership.epoch,
                    now=datetime.now(UTC),
                )
            else:
                try:
                    settlement_ownership = await ownership_repository.claim(
                        run_id=message.run_id,
                        tenant_id=message.tenant_id,
                        owner_id=f"dead-letter:{delivery.delivery_id}",
                        now=datetime.now(UTC),
                        lease_duration=timedelta(seconds=30),
                    )
                except RuntimeOwnershipBusy:
                    raise DeadLetterSettlementPending(self._to_entity(record)) from None
            run = await runs.get_for_update(
                tenant_id=message.tenant_id,
                run_id=message.run_id,
            )
            if run is None:
                raise LookupError(message.run_id)
            if run.status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                await runs.update(
                    run.transition_to(
                        RunStatus.FAILED,
                        error_code=DEAD_LETTER_ERROR_CODE,
                        error_message=None,
                    )
                )
            await commands.mark_processed(message.command_id)
            await ownership_repository.release(
                run_id=message.run_id,
                owner_id=settlement_ownership.owner_id or "",
                epoch=settlement_ownership.epoch,
            )
            record.settled_run_id = message.run_id
            await session.commit()
            return self._to_entity(record)

    async def replay(
        self,
        *,
        tenant_id: UUID,
        dead_letter_id: UUID,
        operator_user_id: UUID,
    ) -> ReplayedRun:
        async with self._session_factory() as session:
            repository = SqlAlchemyRunDeadLetterRepository(session)
            record = await repository.get(
                dead_letter_id,
                tenant_id=tenant_id,
                for_update=True,
            )
            if record is None:
                raise LookupError(dead_letter_id)
            if record.is_malformed:
                raise DeadLetterNotReplayable(dead_letter_id)
            if record.settled_run_id is None:
                raise DeadLetterNotSettled(dead_letter_id)
            if record.replayed_run_id is not None and record.replayed_command_id is not None:
                return ReplayedRun(
                    run_id=record.replayed_run_id,
                    command_id=record.replayed_command_id,
                )
            if record.original_run_id is None or record.tenant_id is None:
                raise DeadLetterNotReplayable(dead_letter_id)
            runs = SqlAlchemyRunRepository(session)
            original = await runs.get(
                tenant_id=record.tenant_id,
                run_id=record.original_run_id,
            )
            if original is None:
                raise LookupError(record.original_run_id)
            replayed_run = Run.create(
                tenant_id=original.tenant_id,
                employee_id=original.employee_id,
                employee_version=original.employee_version,
                created_by=operator_user_id,
                input_data=original.input_data,
            )
            replayed_command = RunCommand.create(
                run_id=replayed_run.id,
                tenant_id=replayed_run.tenant_id,
                action=RunCommandAction.START,
            )
            await runs.add(replayed_run)
            await SqlAlchemyRunCommandRepository(session).add(replayed_command)
            repository.mark_replayed(
                record,
                run_id=replayed_run.id,
                command_id=replayed_command.id,
            )
            await session.commit()
            return ReplayedRun(run_id=replayed_run.id, command_id=replayed_command.id)

    async def reconcile_mirrors(
        self,
        *,
        publisher: DeadLetterMirrorPublisher,
        limit: int = 100,
    ) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        mirrored = 0
        async with self._session_factory() as session:
            candidate_ids = await SqlAlchemyRunDeadLetterRepository(
                session
            ).list_unmirrored_ids_for_worker(limit=limit)
        for dead_letter_id in candidate_ids:
            async with self._session_factory() as session:
                repository = SqlAlchemyRunDeadLetterRepository(session)
                record = await repository.get_unmirrored_for_worker(dead_letter_id)
                if record is None:
                    continue
                try:
                    await publisher.publish_dead_letter(self._to_entity(record))
                except Exception:
                    continue
                repository.mark_mirrored(record)
                await session.commit()
                mirrored += 1
        return mirrored

    @staticmethod
    def _validate_error_type(error_type: str) -> None:
        if error_type not in _ALLOWED_ERROR_TYPES:
            raise ValueError("unsupported dead letter error type")

    async def _existing_after_integrity(
        self,
        delivery_id: str,
        source_stream: str,
        error: IntegrityError,
    ) -> RunDeadLetter:
        async with self._session_factory() as session:
            existing = await SqlAlchemyRunDeadLetterRepository(session).get_by_delivery_id(
                source_stream=source_stream,
                delivery_id=delivery_id,
            )
            if existing is None:
                raise error
            return self._to_entity(existing)

    async def _existing_record_after_integrity(
        self,
        delivery_id: str,
        source_stream: str,
        error: IntegrityError,
    ) -> RunDeadLetterRecord:
        async with self._session_factory() as session:
            existing = await SqlAlchemyRunDeadLetterRepository(session).get_by_delivery_id(
                source_stream=source_stream,
                delivery_id=delivery_id,
            )
            if existing is None:
                raise error
            return existing

    @staticmethod
    def _safe_uuid(value: str | None) -> UUID | None:
        if value is None:
            return None
        try:
            return UUID(value)
        except ValueError:
            return None

    @staticmethod
    def _validate_delivery_binding(
        *,
        command: RunCommand | None,
        run: Run | None,
        delivery: RunQueueDelivery,
    ) -> None:
        message = delivery.message
        if (
            command is None
            or run is None
            or command.run_id != message.run_id
            or command.tenant_id != message.tenant_id
            or command.action.value != message.action
        ):
            raise LookupError(message.command_id)

    @staticmethod
    def _validate_record_binding(
        *,
        record: RunDeadLetterRecord,
        delivery: RunQueueDelivery,
    ) -> None:
        message = delivery.message
        if (
            record.is_malformed
            or record.original_command_id != message.command_id
            or record.original_run_id != message.run_id
            or record.tenant_id != message.tenant_id
            or record.action != message.action
        ):
            raise LookupError(record.id)

    @staticmethod
    def _summarize_fields(raw_fields: dict[str, str]) -> dict[str, JsonValue]:
        keys = sorted(raw_fields)
        canonical = json.dumps(
            raw_fields,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="replace")
        total_bytes = sum(
            len(key.encode("utf-8", errors="replace"))
            + len(value.encode("utf-8", errors="replace"))
            for key, value in raw_fields.items()
        )
        unknown_keys = [key for key in keys if key not in _KNOWN_QUEUE_FIELDS]
        return {
            "known_field_keys": [key for key in keys if key in _KNOWN_QUEUE_FIELDS],
            "unknown_fields": [
                {
                    "length": len(key),
                    "sha256": hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest(),
                }
                for key in unknown_keys[:_MAX_RAW_FIELDS]
            ],
            "field_count": len(keys),
            "total_bytes": total_bytes,
            "sha256": hashlib.sha256(canonical).hexdigest(),
        }

    @staticmethod
    def _to_entity(record: RunDeadLetterRecord) -> RunDeadLetter:
        return RunDeadLetter(
            id=record.id,
            source_stream=record.source_stream,
            original_delivery_id=record.original_delivery_id,
            original_command_id=record.original_command_id,
            original_run_id=record.original_run_id,
            tenant_id=record.tenant_id,
            action=record.action,
            attempts=record.attempts,
            error_type=record.error_type,
            is_malformed=record.is_malformed,
            raw_fields_summary=record.raw_fields_summary,
            failed_at=RunDeadLetterService._as_utc(record.failed_at),
            replayed_run_id=record.replayed_run_id,
            replayed_command_id=record.replayed_command_id,
            replayed_at=(
                RunDeadLetterService._as_utc(record.replayed_at)
                if record.replayed_at is not None
                else None
            ),
            settled_run_id=record.settled_run_id,
            mirrored_at=(
                RunDeadLetterService._as_utc(record.mirrored_at)
                if record.mirrored_at is not None
                else None
            ),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
