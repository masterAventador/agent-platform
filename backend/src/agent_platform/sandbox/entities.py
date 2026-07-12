from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4


class SandboxLeaseStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    DELETING = "deleting"
    DELETED = "deleted"
    EXPIRED = "expired"
    ERROR = "error"


@dataclass(frozen=True)
class SandboxScope:
    """由平台可信任务记录构造的、不可跨 run 复用的隔离身份。"""

    tenant_id: UUID
    user_id: UUID
    run_id: UUID
    thread_id: str

    def __post_init__(self) -> None:
        if not self.thread_id.strip():
            raise ValueError("thread_id 不能为空")


@dataclass(frozen=True)
class SandboxLease:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    run_id: UUID
    thread_id: str
    provider: str
    sandbox_id: str | None
    status: SandboxLeaseStatus
    expires_at: datetime
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def scope(self) -> SandboxScope:
        return SandboxScope(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            run_id=self.run_id,
            thread_id=self.thread_id,
        )

    @classmethod
    def create(
        cls,
        *,
        scope: SandboxScope,
        provider: str,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> SandboxLease:
        timestamp = _utc_now(now)
        _validate_ttl(ttl)
        if not provider.strip():
            raise ValueError("provider 不能为空")
        return cls(
            id=uuid4(),
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            run_id=scope.run_id,
            thread_id=scope.thread_id,
            provider=provider,
            sandbox_id=None,
            status=SandboxLeaseStatus.PROVISIONING,
            expires_at=timestamp + ttl,
            last_error=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def begin_provisioning(self, *, ttl: timedelta, now: datetime | None = None) -> SandboxLease:
        timestamp = _utc_now(now)
        _validate_ttl(ttl)
        return replace(
            self,
            sandbox_id=None,
            status=SandboxLeaseStatus.PROVISIONING,
            expires_at=timestamp + ttl,
            last_error=None,
            updated_at=timestamp,
        )

    def activate(self, sandbox_id: str, *, now: datetime | None = None) -> SandboxLease:
        if not sandbox_id.strip():
            raise ValueError("sandbox_id 不能为空")
        return replace(
            self,
            sandbox_id=sandbox_id,
            status=SandboxLeaseStatus.ACTIVE,
            last_error=None,
            updated_at=_utc_now(now),
        )

    def begin_delete(self, *, now: datetime | None = None) -> SandboxLease:
        return replace(
            self,
            status=SandboxLeaseStatus.DELETING,
            last_error=None,
            updated_at=_utc_now(now),
        )

    def renew(self, *, ttl: timedelta, now: datetime | None = None) -> SandboxLease:
        if self.status is not SandboxLeaseStatus.ACTIVE:
            raise ValueError("只有 active 沙盒租约可以续租")
        _validate_ttl(ttl)
        timestamp = _utc_now(now)
        return replace(self, expires_at=timestamp + ttl, updated_at=timestamp)

    def mark_deleted(self, *, now: datetime | None = None) -> SandboxLease:
        return replace(
            self,
            status=SandboxLeaseStatus.DELETED,
            last_error=None,
            updated_at=_utc_now(now),
        )

    def mark_expired(self, *, now: datetime | None = None) -> SandboxLease:
        return replace(
            self,
            status=SandboxLeaseStatus.EXPIRED,
            last_error=None,
            updated_at=_utc_now(now),
        )

    def mark_error(self, code: str, *, now: datetime | None = None) -> SandboxLease:
        if not code.strip():
            raise ValueError("错误码不能为空")
        return replace(
            self,
            status=SandboxLeaseStatus.ERROR,
            last_error=code,
            updated_at=_utc_now(now),
        )


def _validate_ttl(ttl: timedelta) -> None:
    if ttl <= timedelta(0):
        raise ValueError("沙盒 TTL 必须大于零")


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(UTC)
