from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.sandbox.entities import SandboxLease, SandboxLeaseStatus, SandboxScope
from agent_platform.sandbox.errors import SandboxLeaseScopeConflict


class SandboxLeaseRecord(Base):
    __tablename__ = "sandbox_leases"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(100))
    sandbox_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "run_id",
            "thread_id",
            "provider",
            name="uq_sandbox_leases_scope_provider",
        ),
        UniqueConstraint("provider", "sandbox_id", name="uq_sandbox_leases_provider_sandbox"),
        Index("ix_sandbox_leases_expiry", "status", "expires_at"),
    )


class SqlAlchemySandboxLeaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, lease: SandboxLease) -> None:
        try:
            async with self._session.begin_nested():
                self._session.add(self._to_record(lease))
                await self._session.flush()
        except IntegrityError as error:
            raise SandboxLeaseScopeConflict from error

    async def update(self, lease: SandboxLease) -> None:
        result = await self._session.execute(
            select(SandboxLeaseRecord).where(
                SandboxLeaseRecord.id == lease.id,
                SandboxLeaseRecord.tenant_id == lease.tenant_id,
            )
        )
        record = result.scalar_one()
        record.sandbox_id = lease.sandbox_id
        record.status = lease.status.value
        record.expires_at = lease.expires_at
        record.last_error = lease.last_error
        record.updated_at = lease.updated_at
        await self._session.flush()

    async def get(self, *, tenant_id: UUID, lease_id: UUID) -> SandboxLease | None:
        result = await self._session.execute(
            select(SandboxLeaseRecord).where(
                SandboxLeaseRecord.id == lease_id,
                SandboxLeaseRecord.tenant_id == tenant_id,
            ).with_for_update()
        )
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def get_by_scope(
        self, *, scope: SandboxScope, provider: str
    ) -> SandboxLease | None:
        result = await self._session.execute(
            select(SandboxLeaseRecord).where(
                SandboxLeaseRecord.tenant_id == scope.tenant_id,
                SandboxLeaseRecord.user_id == scope.user_id,
                SandboxLeaseRecord.run_id == scope.run_id,
                SandboxLeaseRecord.thread_id == scope.thread_id,
                SandboxLeaseRecord.provider == provider,
            ).with_for_update()
        )
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list_expired(self, *, now: datetime, limit: int) -> list[SandboxLease]:
        result = await self._session.execute(
            select(SandboxLeaseRecord)
            .where(
                SandboxLeaseRecord.status.in_(
                    [
                        SandboxLeaseStatus.PROVISIONING.value,
                        SandboxLeaseStatus.ACTIVE.value,
                        SandboxLeaseStatus.DELETING.value,
                        SandboxLeaseStatus.ERROR.value,
                    ]
                ),
                SandboxLeaseRecord.expires_at <= now,
            )
            .order_by(SandboxLeaseRecord.expires_at, SandboxLeaseRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [self._to_entity(record) for record in result.scalars()]

    @staticmethod
    def _to_record(lease: SandboxLease) -> SandboxLeaseRecord:
        return SandboxLeaseRecord(
            id=lease.id,
            tenant_id=lease.tenant_id,
            user_id=lease.user_id,
            run_id=lease.run_id,
            thread_id=lease.thread_id,
            provider=lease.provider,
            sandbox_id=lease.sandbox_id,
            status=lease.status.value,
            expires_at=lease.expires_at,
            last_error=lease.last_error,
            created_at=lease.created_at,
            updated_at=lease.updated_at,
        )

    @classmethod
    def _to_entity(cls, record: SandboxLeaseRecord) -> SandboxLease:
        return SandboxLease(
            id=record.id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            run_id=record.run_id,
            thread_id=record.thread_id,
            provider=record.provider,
            sandbox_id=record.sandbox_id,
            status=SandboxLeaseStatus(record.status),
            expires_at=cls._as_utc(record.expires_at),
            last_error=record.last_error,
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemySandboxLeaseUnitOfWork:
    """每次生命周期转换使用独立事务，外部调用前后均形成耐久边界。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._leases: SqlAlchemySandboxLeaseRepository | None = None

    @property
    def leases(self) -> SqlAlchemySandboxLeaseRepository:
        if self._leases is None:
            raise RuntimeError("SandboxLeaseUnitOfWork 尚未进入上下文")
        return self._leases

    async def __aenter__(self) -> SqlAlchemySandboxLeaseUnitOfWork:
        self._session = self._session_factory()
        self._leases = SqlAlchemySandboxLeaseRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._session is not None:
            await self._session.close()
        self._session = None
        self._leases = None

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("SandboxLeaseUnitOfWork 尚未进入上下文")
        await self._session.commit()


class SqlAlchemySandboxLeaseUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqlAlchemySandboxLeaseUnitOfWork:
        return SqlAlchemySandboxLeaseUnitOfWork(self._session_factory)
