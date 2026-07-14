from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agent_platform.platform.model_gateway.errors import InvalidModelGatewayPolicy
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
