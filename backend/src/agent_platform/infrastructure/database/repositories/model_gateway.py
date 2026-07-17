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
    delete,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, aliased, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.model_gateway.entities import (
    TenantModelGatewayKey,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import (
    CorruptModelGatewayPolicy,
    InvalidModelGatewayKey,
    InvalidModelGatewayPolicy,
    ModelGatewayKeyRotationInProgress,
    ModelGatewayPolicyPersistenceError,
    ModelGatewayPolicyRevisionConflict,
)
from agent_platform.platform.model_gateway.ports import (
    ClaimedProvisioningCommand,
    ModelGatewayProvisioningAction,
    ProvisioningCommandStatus,
    ProvisioningHandler,
    ReconcileOutcome,
)


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

    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "desired_revision", "action"),
        CheckConstraint("action = 'reconcile'"),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')"),
        CheckConstraint("desired_revision > 0"),
        CheckConstraint("attempts >= 0"),
    )


class TenantModelGatewayKeyRecord(Base):
    __tablename__ = "tenant_model_gateway_keys"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key_version: Mapped[int] = mapped_column(Integer)
    retired_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("key_version > 0"),
        CheckConstraint(
            "retired_key_version IS NULL OR "
            "(retired_key_version > 0 AND retired_key_version < key_version)"
        ),
    )


class SqlAlchemyModelGatewayKeyRepository:
    """租户 Key 版本的只读访问（Worker 用它派生本租户凭据）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID) -> TenantModelGatewayKey | None:
        try:
            record = await self._session.get(TenantModelGatewayKeyRecord, tenant_id)
        except SQLAlchemyError:
            raise ModelGatewayPolicyPersistenceError from None
        if record is None:
            return None
        try:
            return SqlAlchemyModelGatewayCommandStore._key_to_entity(record)
        except InvalidModelGatewayKey:
            raise CorruptModelGatewayPolicy from None


class SqlAlchemyModelGatewayCommandStore:
    """按租户串行、跨副本独占的 provisioning outbox 认领与结算。

    认领事务横跨真实网关调用：崩溃/断连时行锁自动释放、命令仍为 pending，由任意副本
    自然重入，因此不需要额外的 processing 租约与超时回收。对账调用本身有超时上限，
    锁持有时间因此有界。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def process_next(self, handler: ProvisioningHandler, *, now: datetime) -> bool:
        async with self._session_factory() as session:
            try:
                record = await self._claim(session, now=now)
                if record is None:
                    return False
                key_record = await self._ensure_key(session, tenant_id=record.tenant_id, now=now)
                policy = await self._load_policy(session, tenant_id=record.tenant_id)
                if policy is None:
                    # 策略行随租户级联删除，命令是孤儿：直接结算，不做任何网关调用。
                    record.status = ProvisioningCommandStatus.FAILED.value
                    record.last_error_code = "model_gateway_policy_not_found"
                    record.processed_at = now
                    await session.commit()
                    return True
                claimed = ClaimedProvisioningCommand(
                    command_id=record.id,
                    tenant_id=record.tenant_id,
                    desired_revision=record.desired_revision,
                    action=ModelGatewayProvisioningAction(record.action),
                    attempts=record.attempts,
                    policy=policy,
                    key=self._key_to_entity(key_record),
                )
                outcome = await handler(claimed)
                await self._apply(
                    session,
                    record=record,
                    key_record=key_record,
                    claimed=claimed,
                    outcome=outcome,
                    now=now,
                )
                await session.commit()
                return True
            except SQLAlchemyError:
                await session.rollback()
                raise ModelGatewayPolicyPersistenceError from None
            except BaseException:
                # 含取消：不留半提交状态，命令保持 pending 待重入。
                await session.rollback()
                raise

    async def prune_settled(self, *, older_than: datetime, limit: int) -> int:
        if limit <= 0:
            return 0
        async with self._session_factory() as session:
            try:
                stale = (
                    (
                        await session.execute(
                            select(ModelGatewayProvisioningCommandRecord.id)
                            .where(
                                ModelGatewayProvisioningCommandRecord.status.in_(
                                    (
                                        ProvisioningCommandStatus.COMPLETED.value,
                                        ProvisioningCommandStatus.FAILED.value,
                                    )
                                ),
                                ModelGatewayProvisioningCommandRecord.processed_at.is_not(None),
                                ModelGatewayProvisioningCommandRecord.processed_at < older_than,
                            )
                            .limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not stale:
                    return 0
                result = cast(
                    CursorResult[tuple[object, ...]],
                    await session.execute(
                        delete(ModelGatewayProvisioningCommandRecord).where(
                            ModelGatewayProvisioningCommandRecord.id.in_(stale)
                        )
                    ),
                )
                await session.commit()
                return result.rowcount
            except SQLAlchemyError:
                await session.rollback()
                raise ModelGatewayPolicyPersistenceError from None

    @staticmethod
    async def _claim(
        session: AsyncSession,
        *,
        now: datetime,
    ) -> ModelGatewayProvisioningCommandRecord | None:
        earlier = aliased(ModelGatewayProvisioningCommandRecord)
        # 按租户串行：只认领本租户最早的一条 pending 命令。否则 rev1(enabled)/rev2(disabled)
        # 可能被两个副本并发对账，网关终态取决于调用交错。
        # 租户内以 desired_revision 定序而不是 id：同一毫秒写入的两个 revision 有相同的
        # created_at，用随机 uuid 兜底会让 rev2 抢在 rev1 前对账。
        blocked_by_earlier = (
            select(earlier.id)
            .where(
                earlier.tenant_id == ModelGatewayProvisioningCommandRecord.tenant_id,
                earlier.status == ProvisioningCommandStatus.PENDING.value,
                tuple_(earlier.created_at, earlier.desired_revision)
                < tuple_(
                    ModelGatewayProvisioningCommandRecord.created_at,
                    ModelGatewayProvisioningCommandRecord.desired_revision,
                ),
            )
            .exists()
        )
        result = await session.execute(
            select(ModelGatewayProvisioningCommandRecord)
            .where(
                ModelGatewayProvisioningCommandRecord.status
                == ProvisioningCommandStatus.PENDING.value,
                or_(
                    ModelGatewayProvisioningCommandRecord.next_attempt_at.is_(None),
                    ModelGatewayProvisioningCommandRecord.next_attempt_at <= now,
                ),
                ~blocked_by_earlier,
            )
            .order_by(
                ModelGatewayProvisioningCommandRecord.created_at,
                ModelGatewayProvisioningCommandRecord.desired_revision,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _ensure_key(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        now: datetime,
    ) -> TenantModelGatewayKeyRecord:
        record = await session.get(TenantModelGatewayKeyRecord, tenant_id)
        if record is not None:
            return record
        issued = TenantModelGatewayKey.issue(tenant_id=tenant_id, now=now)
        record = TenantModelGatewayKeyRecord(
            tenant_id=issued.tenant_id,
            key_version=issued.key_version,
            retired_key_version=issued.retired_key_version,
            created_at=issued.created_at,
            updated_at=issued.updated_at,
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def _load_policy(
        session: AsyncSession,
        *,
        tenant_id: UUID,
    ) -> TenantModelGatewayPolicy | None:
        record = await session.get(TenantModelGatewayPolicyRecord, tenant_id)
        try:
            return SqlAlchemyModelGatewayPolicyRepository._to_entity(record)
        except InvalidModelGatewayPolicy:
            raise CorruptModelGatewayPolicy from None

    @staticmethod
    async def _apply(
        session: AsyncSession,
        *,
        record: ModelGatewayProvisioningCommandRecord,
        key_record: TenantModelGatewayKeyRecord,
        claimed: ClaimedProvisioningCommand,
        outcome: ReconcileOutcome,
        now: datetime,
    ) -> None:
        record.attempts += 1
        record.last_error_code = outcome.error_code
        record.status = outcome.command_status.value
        if outcome.command_status is ProvisioningCommandStatus.PENDING:
            record.next_attempt_at = outcome.next_attempt_at
            return
        record.next_attempt_at = None
        record.processed_at = now
        if outcome.clear_key_retirement and key_record.retired_key_version is not None:
            settled = SqlAlchemyModelGatewayCommandStore._key_to_entity(
                key_record
            ).retirement_settled(now=now)
            key_record.retired_key_version = settled.retired_key_version
            key_record.updated_at = settled.updated_at
        if outcome.policy_status is None:
            return
        # revision CAS：对账期间 desired 若已前进，旧结论必须整体丢弃，绝不能把旧
        # revision 的对账结果写成新 desired 的状态（否则 disabled 会被写成 active）。
        await session.execute(
            update(TenantModelGatewayPolicyRecord)
            .where(
                TenantModelGatewayPolicyRecord.tenant_id == claimed.tenant_id,
                TenantModelGatewayPolicyRecord.revision == claimed.desired_revision,
            )
            .values(status=outcome.policy_status.value)
        )

    @staticmethod
    def _key_to_entity(record: TenantModelGatewayKeyRecord) -> TenantModelGatewayKey:
        return TenantModelGatewayKey.restore(
            tenant_id=record.tenant_id,
            key_version=record.key_version,
            retired_key_version=record.retired_key_version,
            created_at=SqlAlchemyModelGatewayPolicyRepository._as_utc(record.created_at),
            updated_at=SqlAlchemyModelGatewayPolicyRepository._as_utc(record.updated_at),
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

    async def get_key(self, tenant_id: UUID) -> TenantModelGatewayKey | None:
        return await SqlAlchemyModelGatewayKeyRepository(self._session).get(tenant_id)

    async def save_rotated_key(
        self,
        policy: TenantModelGatewayPolicy,
        *,
        key: TenantModelGatewayKey,
        expected_revision: int,
        action: ModelGatewayProvisioningAction,
    ) -> None:
        """同一事务内递增 Key 版本 + 推进 desired revision + 入队对账命令。

        三者必须原子：只写其中一部分会让网关侧与 desired 永久不一致（例如版本已递增
        但没有命令去创建新 Key，租户会一直用不存在的凭据）。
        """
        try:
            result = cast(
                CursorResult[tuple[object, ...]],
                await self._session.execute(
                    update(TenantModelGatewayKeyRecord)
                    .where(
                        TenantModelGatewayKeyRecord.tenant_id == key.tenant_id,
                        TenantModelGatewayKeyRecord.key_version == key.key_version - 1,
                        TenantModelGatewayKeyRecord.retired_key_version.is_(None),
                    )
                    .values(
                        key_version=key.key_version,
                        retired_key_version=key.retired_key_version,
                        updated_at=key.updated_at,
                    )
                ),
            )
            if result.rowcount != 1:
                await self._session.rollback()
                raise ModelGatewayKeyRotationInProgress
            await self.save_desired(
                policy,
                expected_revision=expected_revision,
                action=action,
            )
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
