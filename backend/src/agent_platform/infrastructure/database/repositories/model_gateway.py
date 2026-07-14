from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.model_gateway.entities import TenantModelGatewayPolicy
from agent_platform.platform.model_gateway.errors import (
    CorruptModelGatewayPolicy,
    InvalidModelGatewayPolicy,
    ModelGatewayPolicyPersistenceError,
    ModelGatewayPolicyRevisionConflict,
)
from agent_platform.platform.model_gateway.ports import ModelGatewayProvisioningAction


class TenantModelGatewayPolicyRecord(Base):
    __tablename__ = "tenant_model_gateway_policies"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean)
    allowed_aliases: Mapped[list[str]] = mapped_column(JSON)
    budget_microusd: Mapped[int] = mapped_column(BigInteger)
    budget_period: Mapped[str] = mapped_column(String(16))
    rpm_limit: Mapped[int] = mapped_column(Integer)
    tpm_limit: Mapped[int] = mapped_column(Integer)
    max_parallel_requests: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint("budget_microusd > 0"),
        CheckConstraint("budget_period = 'monthly'"),
        CheckConstraint("rpm_limit > 0"),
        CheckConstraint("tpm_limit > 0"),
        CheckConstraint("max_parallel_requests > 0"),
        CheckConstraint("revision > 0"),
        CheckConstraint("status IN ('pending', 'active', 'disabled', 'error')"),
    )


class ModelGatewayProvisioningCommandRecord(Base):
    __tablename__ = "model_gateway_provisioning_commands"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    desired_revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "desired_revision", "action"),
        CheckConstraint("action = 'reconcile'"),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')"),
        CheckConstraint("desired_revision > 0"),
        CheckConstraint("attempts >= 0"),
    )


class SqlAlchemyModelGatewayPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID) -> TenantModelGatewayPolicy | None:
        try:
            record = await self._session.get(TenantModelGatewayPolicyRecord, tenant_id)
            return self._to_entity(record)
        except InvalidModelGatewayPolicy:
            raise CorruptModelGatewayPolicy from None
        except SQLAlchemyError:
            raise ModelGatewayPolicyPersistenceError from None

    async def save_desired(
        self,
        policy: TenantModelGatewayPolicy,
        *,
        expected_revision: int,
        action: ModelGatewayProvisioningAction,
    ) -> None:
        try:
            if expected_revision == 0:
                self._session.add(self._to_record(policy))
            else:
                result = cast(
                    CursorResult[tuple[object, ...]],
                    await self._session.execute(
                        update(TenantModelGatewayPolicyRecord)
                        .where(
                            TenantModelGatewayPolicyRecord.tenant_id == policy.tenant_id,
                            TenantModelGatewayPolicyRecord.revision == expected_revision,
                        )
                        .values(
                            enabled=policy.enabled,
                            allowed_aliases=sorted(policy.allowed_aliases),
                            budget_microusd=policy.budget_microusd,
                            budget_period=policy.budget_period.value,
                            rpm_limit=policy.rpm_limit,
                            tpm_limit=policy.tpm_limit,
                            max_parallel_requests=policy.max_parallel_requests,
                            revision=policy.revision,
                            status=policy.status.value,
                            updated_at=policy.updated_at,
                            updated_by=policy.updated_by,
                        )
                    ),
                )
                if result.rowcount != 1:
                    await self._session.rollback()
                    raise ModelGatewayPolicyRevisionConflict
            self._session.add(
                ModelGatewayProvisioningCommandRecord(
                    id=uuid4(),
                    tenant_id=policy.tenant_id,
                    desired_revision=policy.revision,
                    action=action.value,
                    status="pending",
                    attempts=0,
                    last_error_code=None,
                    created_at=policy.updated_at,
                    processed_at=None,
                )
            )
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            try:
                if await self._is_confirmed_conflict(
                    policy=policy,
                    expected_revision=expected_revision,
                    action=action,
                ):
                    raise ModelGatewayPolicyRevisionConflict from None
            except SQLAlchemyError:
                raise ModelGatewayPolicyPersistenceError from None
            raise ModelGatewayPolicyPersistenceError from None
        except SQLAlchemyError:
            await self._session.rollback()
            raise ModelGatewayPolicyPersistenceError from None

    async def _is_confirmed_conflict(
        self,
        *,
        policy: TenantModelGatewayPolicy,
        expected_revision: int,
        action: ModelGatewayProvisioningAction,
    ) -> bool:
        if expected_revision == 0:
            return (
                await self._session.get(TenantModelGatewayPolicyRecord, policy.tenant_id)
            ) is not None
        result = await self._session.execute(
            select(ModelGatewayProvisioningCommandRecord.id).where(
                ModelGatewayProvisioningCommandRecord.tenant_id == policy.tenant_id,
                ModelGatewayProvisioningCommandRecord.desired_revision == policy.revision,
                ModelGatewayProvisioningCommandRecord.action == action.value,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    def _to_record(policy: TenantModelGatewayPolicy) -> TenantModelGatewayPolicyRecord:
        return TenantModelGatewayPolicyRecord(
            tenant_id=policy.tenant_id,
            enabled=policy.enabled,
            allowed_aliases=sorted(policy.allowed_aliases),
            budget_microusd=policy.budget_microusd,
            budget_period=policy.budget_period.value,
            rpm_limit=policy.rpm_limit,
            tpm_limit=policy.tpm_limit,
            max_parallel_requests=policy.max_parallel_requests,
            revision=policy.revision,
            status=policy.status.value,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            updated_by=policy.updated_by,
        )

    @classmethod
    def _to_entity(
        cls, record: TenantModelGatewayPolicyRecord | None
    ) -> TenantModelGatewayPolicy | None:
        if record is None:
            return None
        return TenantModelGatewayPolicy.restore(
            tenant_id=record.tenant_id,
            enabled=record.enabled,
            allowed_aliases=record.allowed_aliases,
            budget_microusd=record.budget_microusd,
            budget_period=record.budget_period,
            rpm_limit=record.rpm_limit,
            tpm_limit=record.tpm_limit,
            max_parallel_requests=record.max_parallel_requests,
            revision=record.revision,
            status=record.status,
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
            updated_by=record.updated_by,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
