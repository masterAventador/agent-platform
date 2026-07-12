from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base


class RuntimeOwnershipBusy(RuntimeError):
    """另一个仍存活的 Worker 持有该 run 的执行权。"""


class RuntimeOwnershipLost(RuntimeError):
    """当前 Worker 的 epoch 已过期，禁止继续写入运行结果。"""

    def __init__(self, run_id: UUID) -> None:
        super().__init__(run_id)
        self._run_id = run_id

    @property
    def run_id(self) -> UUID:
        return self._run_id


@dataclass(frozen=True, slots=True)
class RuntimeOwnership:
    run_id: UUID
    tenant_id: UUID
    owner_id: str | None
    epoch: int
    expires_at: datetime
    updated_at: datetime


class RuntimeOwnershipRecord(Base):
    __tablename__ = "runtime_ownership"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    owner_id: Mapped[str | None] = mapped_column(String(200), index=True, nullable=True)
    epoch: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SqlAlchemyRuntimeOwnershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, run_id: UUID) -> RuntimeOwnership | None:
        record = await self._session.get(RuntimeOwnershipRecord, run_id)
        return self._to_entity(record) if record is not None else None

    async def claim(
        self,
        *,
        run_id: UUID,
        tenant_id: UUID,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> RuntimeOwnership:
        timestamp = self._utc(now)
        if lease_duration <= timedelta(0):
            raise ValueError("runtime lease duration must be positive")
        if not owner_id.strip() or owner_id != owner_id.strip():
            raise ValueError("owner_id must be canonical")
        record = await self._locked(run_id)
        expires_at = timestamp + lease_duration
        if record is None:
            try:
                async with self._session.begin_nested():
                    self._session.add(
                        RuntimeOwnershipRecord(
                            run_id=run_id,
                            tenant_id=tenant_id,
                            owner_id=owner_id,
                            epoch=1,
                            expires_at=expires_at,
                            updated_at=timestamp,
                        )
                    )
                    await self._session.flush()
            except IntegrityError:
                record = await self._locked(run_id)
                if record is None:
                    raise RuntimeOwnershipLost(run_id) from None
            else:
                record = await self._locked(run_id)
                if record is None:
                    raise RuntimeOwnershipLost(run_id)
        assert record is not None
        if record.owner_id == owner_id and self._utc(record.expires_at) > timestamp:
            record.expires_at = expires_at
            record.updated_at = timestamp
        elif record.tenant_id != tenant_id:
            raise RuntimeOwnershipLost(run_id)
        elif record.owner_id is not None and self._utc(record.expires_at) > timestamp:
            raise RuntimeOwnershipBusy(run_id)
        else:
            record.owner_id = owner_id
            record.epoch += 1
            record.expires_at = expires_at
            record.updated_at = timestamp
        await self._session.flush()
        return self._to_entity(record)

    async def assert_owned(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        epoch: int,
        now: datetime,
    ) -> None:
        result = await self._session.execute(
            select(RuntimeOwnershipRecord.run_id)
            .where(
                RuntimeOwnershipRecord.run_id == run_id,
                RuntimeOwnershipRecord.owner_id == owner_id,
                RuntimeOwnershipRecord.epoch == epoch,
                RuntimeOwnershipRecord.expires_at > self._utc(now),
            )
            .with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise RuntimeOwnershipLost(run_id)

    async def release(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        epoch: int,
        now: datetime | None = None,
    ) -> bool:
        result = await self._session.execute(
            select(RuntimeOwnershipRecord)
            .where(
                RuntimeOwnershipRecord.run_id == run_id,
                RuntimeOwnershipRecord.owner_id == owner_id,
                RuntimeOwnershipRecord.epoch == epoch,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            return False
        timestamp = self._utc(now or datetime.now(UTC))
        record.owner_id = None
        record.expires_at = timestamp
        record.updated_at = timestamp
        await self._session.flush()
        return True

    async def _locked(self, run_id: UUID) -> RuntimeOwnershipRecord | None:
        result = await self._session.execute(
            select(RuntimeOwnershipRecord)
            .where(RuntimeOwnershipRecord.run_id == run_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @classmethod
    def _to_entity(cls, record: RuntimeOwnershipRecord) -> RuntimeOwnership:
        return RuntimeOwnership(
            run_id=record.run_id,
            tenant_id=record.tenant_id,
            owner_id=record.owner_id,
            epoch=record.epoch,
            expires_at=cls._utc(record.expires_at),
            updated_at=cls._utc(record.updated_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
