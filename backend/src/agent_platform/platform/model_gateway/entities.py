from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agent_platform.platform.model_gateway.errors import (
    InvalidModelGatewayKey,
    InvalidModelGatewayPolicy,
    ModelGatewayKeyRotationInProgress,
)
from agent_platform.platform.models import DEFAULT_MODEL_ALIASES

MAX_BUDGET_MICROUSD = 2**53 - 1
MAX_SIGNED_INT32 = 2**31 - 1


class ModelGatewayBudgetPeriod(StrEnum):
    MONTHLY = "monthly"


class ModelGatewayPolicyStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TenantModelGatewayPolicy:
    tenant_id: UUID
    enabled: bool
    allowed_aliases: frozenset[str]
    budget_microusd: int
    budget_period: ModelGatewayBudgetPeriod
    rpm_limit: int
    tpm_limit: int
    max_parallel_requests: int
    revision: int
    status: ModelGatewayPolicyStatus
    created_at: datetime
    updated_at: datetime
    updated_by: UUID

    @classmethod
    def create_desired(
        cls,
        *,
        tenant_id: UUID,
        enabled: bool,
        allowed_aliases: Collection[str],
        budget_microusd: int,
        budget_period: str | ModelGatewayBudgetPeriod,
        rpm_limit: int,
        tpm_limit: int,
        max_parallel_requests: int,
        revision: int,
        updated_by: UUID,
        now: datetime,
    ) -> "TenantModelGatewayPolicy":
        aliases = cls._normalize_aliases(allowed_aliases)
        cls._validate(
            enabled=enabled,
            aliases=aliases,
            budget_microusd=budget_microusd,
            budget_period=budget_period,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            max_parallel_requests=max_parallel_requests,
            revision=revision,
            now=now,
        )
        return cls(
            tenant_id=tenant_id,
            enabled=enabled,
            allowed_aliases=aliases,
            budget_microusd=budget_microusd,
            budget_period=ModelGatewayBudgetPeriod(budget_period),
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            max_parallel_requests=max_parallel_requests,
            revision=revision,
            status=ModelGatewayPolicyStatus.PENDING,
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )

    def revise_desired(
        self,
        *,
        enabled: bool,
        allowed_aliases: Collection[str],
        budget_microusd: int,
        budget_period: str | ModelGatewayBudgetPeriod,
        rpm_limit: int,
        tpm_limit: int,
        max_parallel_requests: int,
        updated_by: UUID,
        now: datetime,
    ) -> "TenantModelGatewayPolicy":
        revised = self.create_desired(
            tenant_id=self.tenant_id,
            enabled=enabled,
            allowed_aliases=allowed_aliases,
            budget_microusd=budget_microusd,
            budget_period=budget_period,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            max_parallel_requests=max_parallel_requests,
            revision=self.revision + 1,
            updated_by=updated_by,
            now=now,
        )
        return TenantModelGatewayPolicy(
            tenant_id=revised.tenant_id,
            enabled=revised.enabled,
            allowed_aliases=revised.allowed_aliases,
            budget_microusd=revised.budget_microusd,
            budget_period=revised.budget_period,
            rpm_limit=revised.rpm_limit,
            tpm_limit=revised.tpm_limit,
            max_parallel_requests=revised.max_parallel_requests,
            revision=revised.revision,
            status=revised.status,
            created_at=self.created_at,
            updated_at=revised.updated_at,
            updated_by=revised.updated_by,
        )

    @classmethod
    def restore(
        cls,
        *,
        tenant_id: UUID,
        enabled: bool,
        allowed_aliases: Collection[str],
        budget_microusd: int,
        budget_period: str | ModelGatewayBudgetPeriod,
        rpm_limit: int,
        tpm_limit: int,
        max_parallel_requests: int,
        revision: int,
        status: str | ModelGatewayPolicyStatus,
        created_at: datetime,
        updated_at: datetime,
        updated_by: UUID,
    ) -> "TenantModelGatewayPolicy":
        aliases = cls._normalize_aliases(allowed_aliases)
        cls._validate(
            enabled=enabled,
            aliases=aliases,
            budget_microusd=budget_microusd,
            budget_period=budget_period,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            max_parallel_requests=max_parallel_requests,
            revision=revision,
            now=updated_at,
        )
        cls._validate_timestamp(created_at)
        if updated_at < created_at:
            raise InvalidModelGatewayPolicy("updated_at must not precede created_at")
        try:
            restored_status = ModelGatewayPolicyStatus(status)
        except ValueError as error:
            raise InvalidModelGatewayPolicy("unsupported policy status") from error
        if (not enabled and restored_status is ModelGatewayPolicyStatus.ACTIVE) or (
            enabled and restored_status is ModelGatewayPolicyStatus.DISABLED
        ):
            raise InvalidModelGatewayPolicy("enabled and policy status are inconsistent")
        return cls(
            tenant_id=tenant_id,
            enabled=enabled,
            allowed_aliases=aliases,
            budget_microusd=budget_microusd,
            budget_period=ModelGatewayBudgetPeriod(budget_period),
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            max_parallel_requests=max_parallel_requests,
            revision=revision,
            status=restored_status,
            created_at=created_at,
            updated_at=updated_at,
            updated_by=updated_by,
        )

    @staticmethod
    def _validate(
        *,
        enabled: bool,
        aliases: frozenset[str],
        budget_microusd: int,
        budget_period: str | ModelGatewayBudgetPeriod,
        rpm_limit: int,
        tpm_limit: int,
        max_parallel_requests: int,
        revision: int,
        now: datetime,
    ) -> None:
        if type(enabled) is not bool:
            raise InvalidModelGatewayPolicy("enabled must be a boolean")
        if not aliases or not aliases.issubset(DEFAULT_MODEL_ALIASES):
            raise InvalidModelGatewayPolicy("allowed aliases are not supported")
        if (
            type(budget_microusd) is not int
            or budget_microusd <= 0
            or budget_microusd > MAX_BUDGET_MICROUSD
        ):
            raise InvalidModelGatewayPolicy("budget exceeds the safe gateway precision range")
        try:
            ModelGatewayBudgetPeriod(budget_period)
        except ValueError as error:
            raise InvalidModelGatewayPolicy("unsupported budget period") from error
        for value in (rpm_limit, tpm_limit, max_parallel_requests, revision):
            if type(value) is not int or value <= 0 or value > MAX_SIGNED_INT32:
                raise InvalidModelGatewayPolicy(
                    "limits and revision must be positive signed int32 values"
                )
        TenantModelGatewayPolicy._validate_timestamp(now)

    @staticmethod
    def _normalize_aliases(allowed_aliases: Collection[str]) -> frozenset[str]:
        if not isinstance(allowed_aliases, (list, tuple, set, frozenset)) or any(
            not isinstance(alias, str) for alias in allowed_aliases
        ):
            raise InvalidModelGatewayPolicy("allowed aliases must be strings")
        aliases = frozenset(allowed_aliases)
        if len(aliases) != len(allowed_aliases):
            raise InvalidModelGatewayPolicy("allowed aliases must be unique")
        return aliases

    @staticmethod
    def _validate_timestamp(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidModelGatewayPolicy("timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TenantModelGatewayKey:
    """租户虚拟 Key 的可归因/可撤销生命周期状态。

    只保存版本号：Key 明文与其 SHA256 摘要都由 ``credentials`` 按
    (服务端密钥, tenant_id, key_version) 现场派生，因此数据库里不存在任何由 Key 派生的
    材料，只有持有服务端密钥的进程（Controller、Worker）才能得到凭据本身。

    两个版本号承担**不同语义**，不可混用：

    - ``key_version`` 是 desired：平台希望网关上存在的版本；
    - ``provisioned_key_version`` 是 observed：Controller 已在真实网关确认存在且未被阻断的
      版本，``None`` 表示网关侧当前没有可用 Key。**Worker 必须用它派生凭据**，因为它是
      「网关侧真实存在」的唯一真相源；用 desired 派生会在对账窗口里拿到网关上还不存在的 Key。
      它只能由真实对账的真实副作用写入，因此不可能被 Seed 或测试伪造成终态。

    ``retired_key_version`` 是待 Controller 在真实网关删除的上一版本，未回收前禁止再次
    轮换，避免旧版本 Key 在 LiteLLM 侧成为无人回收的孤儿。
    """

    tenant_id: UUID
    key_version: int
    retired_key_version: int | None
    provisioned_key_version: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def issue(cls, *, tenant_id: UUID, now: datetime) -> "TenantModelGatewayKey":
        return cls.restore(
            tenant_id=tenant_id,
            key_version=1,
            retired_key_version=None,
            # 尚未对账：网关侧还没有任何 Key。
            provisioned_key_version=None,
            created_at=now,
            updated_at=now,
        )

    def rotate(self, *, now: datetime) -> "TenantModelGatewayKey":
        if self.retired_key_version is not None:
            raise ModelGatewayKeyRotationInProgress
        if self.key_version >= MAX_SIGNED_INT32:
            raise InvalidModelGatewayKey("key version exhausted")
        return TenantModelGatewayKey.restore(
            tenant_id=self.tenant_id,
            key_version=self.key_version + 1,
            retired_key_version=self.key_version,
            # 轮换只改 desired：新版本在网关上真实建立前，observed 必须保持不变，
            # 否则轮换落库到对账完成之间会出现一个凭据真空期。
            provisioned_key_version=self.provisioned_key_version,
            created_at=self.created_at,
            updated_at=now,
        )

    def reconciled(
        self,
        *,
        provisioned: bool,
        clear_retirement: bool,
        now: datetime,
    ) -> "TenantModelGatewayKey":
        """记录一次真实对账的观测结果。

        ``provisioned=False``（策略停用对账完成）必须清空 observed：此刻网关侧的 Key 已被
        阻断，若继续声称可用，再启用的窗口里 Worker 会拿着 blocked Key 去撞 401。
        """
        return TenantModelGatewayKey.restore(
            tenant_id=self.tenant_id,
            key_version=self.key_version,
            retired_key_version=None if clear_retirement else self.retired_key_version,
            provisioned_key_version=self.key_version if provisioned else None,
            created_at=self.created_at,
            updated_at=now,
        )

    @classmethod
    def restore(
        cls,
        *,
        tenant_id: UUID,
        key_version: int,
        retired_key_version: int | None,
        provisioned_key_version: int | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> "TenantModelGatewayKey":
        if not isinstance(tenant_id, UUID):
            raise InvalidModelGatewayKey("tenant_id must be a UUID")
        checked_version = cls._validate_version(key_version)
        checked_retired = (
            None if retired_key_version is None else cls._validate_version(retired_key_version)
        )
        checked_provisioned = (
            None
            if provisioned_key_version is None
            else cls._validate_version(provisioned_key_version)
        )
        if checked_retired is not None and checked_retired >= checked_version:
            raise InvalidModelGatewayKey("retired key version must precede the active one")
        if checked_provisioned is not None and checked_provisioned > checked_version:
            raise InvalidModelGatewayKey(
                "provisioned key version must not exceed the desired one"
            )
        for value in (created_at, updated_at):
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise InvalidModelGatewayKey("timestamps must be timezone-aware")
        if updated_at < created_at:
            raise InvalidModelGatewayKey("updated_at must not precede created_at")
        return cls(
            tenant_id=tenant_id,
            key_version=checked_version,
            retired_key_version=checked_retired,
            provisioned_key_version=checked_provisioned,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _validate_version(value: int) -> int:
        if type(value) is not int or value <= 0 or value > MAX_SIGNED_INT32:
            raise InvalidModelGatewayKey("key version must be a positive signed int32 value")
        return value
