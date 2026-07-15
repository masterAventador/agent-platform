from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol, TypeVar
from uuid import UUID, uuid4

_ResultT = TypeVar("_ResultT")


class AuthorizationError(PermissionError):
    """The caller cannot perform the requested Social Operations action."""


class ResourceNotFoundError(LookupError):
    """A tenant-scoped Social Operations resource is unavailable."""


class ConflictError(RuntimeError):
    """A stable identifier or state transition conflicts with existing state."""


class DevicePlatform(StrEnum):
    MACOS = "macos"
    WINDOWS = "windows"


class SocialPlatform(StrEnum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    KUAISHOU = "kuaishou"
    WECHAT_CHANNELS = "wechat_channels"
    WECHAT = "wechat"


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    EMERGENCY_STOPPED = "emergency_stopped"


class LocalTaskStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    CANCELLED = "cancelled"


class AccountStatus(StrEnum):
    AWAITING_SCAN = "awaiting_scan"
    HEALTHY = "healthy"
    HUMAN_HANDOFF = "human_handoff"
    LOGGED_OUT = "logged_out"


class AccountHealthSignal(StrEnum):
    AUTHENTICATED = "authenticated"
    CAPTCHA_REQUIRED = "captcha_required"
    RISK_CONTROL = "risk_control"
    LOGIN_EXPIRED = "login_expired"


class EmergencyStopReason(StrEnum):
    OPERATOR_REQUESTED = "operator_requested"
    POLICY_VIOLATION = "policy_violation"
    DEVICE_COMPROMISE_SUSPECTED = "device_compromise_suspected"


class AccountHandoffReason(StrEnum):
    CAPTCHA_REQUIRED = "captcha_required"
    RISK_CONTROL = "risk_control"
    LOGIN_EXPIRED = "login_expired"
    DEVICE_EMERGENCY_STOPPED = "device_emergency_stopped"


@dataclass(frozen=True, slots=True)
class ActorContext:
    tenant_id: UUID
    user_id: UUID
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class Device:
    device_id: UUID
    tenant_id: UUID
    owner_user_id: UUID
    display_name: str
    platform: DevicePlatform
    app_version: str
    executor_version: str
    registered_at: datetime
    last_seen_at: datetime
    status: DeviceStatus
    heartbeat_sequence: int


@dataclass(frozen=True, slots=True)
class LocalTask:
    task_id: UUID
    tenant_id: UUID
    requested_by_user_id: UUID
    target_device_id: UUID
    task_type: str
    status: LocalTaskStatus
    created_at: datetime
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    claim_attempt: int = 0


@dataclass(frozen=True, slots=True)
class PlatformAccount:
    account_id: UUID
    tenant_id: UUID
    owner_user_id: UUID
    device_id: UUID
    platform: SocialPlatform
    display_name: str
    status: AccountStatus
    circuit_open: bool
    handoff_reason: AccountHandoffReason | None
    session_revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: UUID
    action: str
    tenant_id: UUID
    actor_user_id: UUID
    resource_id: UUID
    occurred_at: datetime
    details: tuple[tuple[str, str], ...] = ()


class InMemoryAuditSink:
    """Deterministic test sink that stores only structured, non-secret details."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._event_ids: set[UUID] = set()

    def record(self, event: AuditEvent) -> None:
        if event.event_id in self._event_ids:
            return
        self._event_ids.add(event.event_id)
        self.events.append(event)


class AuditPort(Protocol):
    """Adapter seam for the Core audit application port introduced by C14."""

    def record(self, event: AuditEvent) -> None: ...


class DeviceAccountStateStore(Protocol):
    """Capability persistence seam; the Core PostgreSQL adapter is wired centrally."""

    def load(self) -> tuple[int, Mapping[str, Any]] | None: ...

    def save(self, state: Mapping[str, Any], *, expected_revision: int) -> int: ...


class DeviceAccountService:
    """Capability-owned application service pending C14/C17 production adapters."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        audit_sink: AuditPort,
        offline_after: timedelta,
        claim_lease: timedelta,
        state_store: DeviceAccountStateStore | None = None,
    ) -> None:
        if offline_after <= timedelta(0) or claim_lease <= timedelta(0):
            raise ValueError("timeouts must be positive")
        self._clock = clock
        self._audit_sink = audit_sink
        self._state_store = state_store
        self._offline_after = offline_after
        self._claim_lease = claim_lease
        self._lock = RLock()
        self._devices: dict[UUID, Device] = {}
        self._tasks: dict[UUID, LocalTask] = {}
        self._accounts: dict[UUID, PlatformAccount] = {}
        self._audit_outbox: list[AuditEvent] = []
        self._state_revision = 0
        if state_store is not None:
            snapshot = state_store.load()
            if snapshot is not None:
                self._state_revision, state = snapshot
                self._restore_state(state)
            self._drain_audit_outbox()

    def register_device(
        self,
        actor: ActorContext,
        *,
        device_id: UUID,
        display_name: str,
        platform: DevicePlatform,
        app_version: str,
        executor_version: str,
        heartbeat_sequence: int,
    ) -> Device:
        self._require_permission(actor, "social.execute")
        self._validate_text(display_name, "display name")
        self._validate_text(app_version, "app version")
        self._validate_text(executor_version, "executor version")
        if heartbeat_sequence != 0:
            raise ValueError("initial heartbeat sequence must be zero")
        with self._lock:
            existing = self._devices.get(device_id)
            if existing is not None and (
                existing.tenant_id != actor.tenant_id
                or existing.owner_user_id != actor.user_id
            ):
                raise ConflictError("device identifier is already registered")
            if existing is not None and existing.platform is not platform:
                raise ConflictError("device platform cannot be changed")
            now = self._clock()
            effective_existing = (
                self._device_with_online_state(existing) if existing is not None else None
            )
            device = Device(
                device_id=device_id,
                tenant_id=actor.tenant_id,
                owner_user_id=actor.user_id,
                display_name=display_name,
                platform=platform,
                app_version=app_version,
                executor_version=executor_version,
                registered_at=existing.registered_at if existing else now,
                last_seen_at=existing.last_seen_at if existing else now,
                status=(
                    effective_existing.status
                    if effective_existing is not None
                    else DeviceStatus.ONLINE
                ),
                heartbeat_sequence=(
                    existing.heartbeat_sequence if existing else heartbeat_sequence
                ),
            )

            def mutate() -> Device:
                self._devices[device_id] = device
                return device

            return self._commit_with_audit(
                actor,
                "social.device.registered",
                device_id,
                mutate,
                platform=platform.value,
            )

    def heartbeat(
        self,
        actor: ActorContext,
        *,
        device_id: UUID,
        app_version: str,
        executor_version: str,
        heartbeat_sequence: int,
    ) -> Device:
        self._require_permission(actor, "social.execute")
        with self._lock:
            device = self._owned_device(actor, device_id)
            self._validate_text(app_version, "app version")
            self._validate_text(executor_version, "executor version")
            if heartbeat_sequence <= device.heartbeat_sequence:
                if (
                    heartbeat_sequence == device.heartbeat_sequence
                    and app_version == device.app_version
                    and executor_version == device.executor_version
                ):
                    return self._device_with_online_state(device)
                raise ConflictError("heartbeat sequence is stale or conflicting")
            status = (
                DeviceStatus.EMERGENCY_STOPPED
                if device.status is DeviceStatus.EMERGENCY_STOPPED
                else DeviceStatus.ONLINE
            )
            updated = replace(
                device,
                app_version=app_version,
                executor_version=executor_version,
                last_seen_at=self._clock(),
                status=status,
                heartbeat_sequence=heartbeat_sequence,
            )
            return self._commit_state(
                lambda: self._replace_device(device_id, updated)
            )

    def get_device(self, actor: ActorContext, device_id: UUID) -> Device:
        self._require_permission(actor, "social.read")
        with self._lock:
            return self._device_with_online_state(self._owned_device(actor, device_id))

    def list_devices(self, actor: ActorContext) -> tuple[Device, ...]:
        self._require_permission(actor, "social.read")
        with self._lock:
            return tuple(
                self._device_with_online_state(device)
                for device in sorted(self._devices.values(), key=lambda item: item.device_id.int)
                if device.tenant_id == actor.tenant_id
                and device.owner_user_id == actor.user_id
            )

    def enqueue_task(
        self,
        actor: ActorContext,
        *,
        task_id: UUID,
        target_device_id: UUID,
        task_type: str,
    ) -> LocalTask:
        self._require_permission(actor, "social.execute")
        self._validate_text(task_type, "task type")
        with self._lock:
            device = self._owned_device(actor, target_device_id)
            if device.status is DeviceStatus.EMERGENCY_STOPPED:
                raise AuthorizationError("device emergency stop is active")
            if task_id in self._tasks:
                raise ConflictError("task identifier already exists")
            task = LocalTask(
                task_id=task_id,
                tenant_id=actor.tenant_id,
                requested_by_user_id=actor.user_id,
                target_device_id=target_device_id,
                task_type=task_type,
                status=LocalTaskStatus.QUEUED,
                created_at=self._clock(),
            )

            def mutate() -> LocalTask:
                self._tasks[task_id] = task
                return task

            return self._commit_with_audit(
                actor,
                "social.local_task.queued",
                task_id,
                mutate,
                device_id=str(target_device_id),
            )

    def claim_tasks(
        self,
        actor: ActorContext,
        device_id: UUID,
        *,
        limit: int,
    ) -> tuple[LocalTask, ...]:
        self._require_permission(actor, "social.execute")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            device = self._device_with_online_state(self._owned_device(actor, device_id))
            if device.status is not DeviceStatus.ONLINE:
                return ()
            now = self._clock()
            proposed_tasks = self._tasks.copy()
            for task_id, task in tuple(proposed_tasks.items()):
                if (
                    task.tenant_id == actor.tenant_id
                    and task.target_device_id == device_id
                    and task.status is LocalTaskStatus.CLAIMED
                    and task.lease_expires_at is not None
                    and task.lease_expires_at <= now
                ):
                    proposed_tasks[task_id] = replace(
                        task,
                        status=LocalTaskStatus.QUEUED,
                        claimed_at=None,
                        lease_expires_at=None,
                    )
            candidates = sorted(
                (
                    task
                    for task in proposed_tasks.values()
                    if task.tenant_id == actor.tenant_id
                    and task.target_device_id == device_id
                    and task.status is LocalTaskStatus.QUEUED
                ),
                key=lambda task: (task.created_at, task.task_id.int),
            )[:limit]
            claimed = tuple(
                replace(
                    task,
                    status=LocalTaskStatus.CLAIMED,
                    claimed_at=now,
                    lease_expires_at=now + self._claim_lease,
                    claim_attempt=task.claim_attempt + 1,
                )
                for task in candidates
            )
            if not claimed:
                return ()

            def mutate() -> tuple[LocalTask, ...]:
                proposed_tasks.update({task.task_id: task for task in claimed})
                self._tasks = proposed_tasks
                return claimed

            return self._commit_state(mutate)

    def get_task(self, actor: ActorContext, task_id: UUID) -> LocalTask:
        self._require_permission(actor, "social.read")
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.tenant_id != actor.tenant_id:
                raise ResourceNotFoundError("local task not found")
            self._owned_device(actor, task.target_device_id)
            return task

    def emergency_stop(
        self,
        actor: ActorContext,
        device_id: UUID,
        *,
        reason: EmergencyStopReason,
    ) -> Device:
        self._require_permission(actor, "social.manage")
        if not isinstance(reason, EmergencyStopReason):
            raise ValueError("invalid emergency stop reason")
        with self._lock:
            device = self._owned_device(actor, device_id)
            stopped = replace(device, status=DeviceStatus.EMERGENCY_STOPPED)
            now = self._clock()

            def mutate() -> Device:
                self._devices[device_id] = stopped
                for task_id, task in tuple(self._tasks.items()):
                    if (
                        task.tenant_id == actor.tenant_id
                        and task.target_device_id == device_id
                        and task.status
                        in {LocalTaskStatus.QUEUED, LocalTaskStatus.CLAIMED}
                    ):
                        self._tasks[task_id] = replace(
                            task, status=LocalTaskStatus.CANCELLED
                        )
                for account_id, account in tuple(self._accounts.items()):
                    if account.device_id != device_id:
                        continue
                    self._accounts[account_id] = replace(
                        account,
                        status=(
                            AccountStatus.LOGGED_OUT
                            if account.status is AccountStatus.LOGGED_OUT
                            else AccountStatus.HUMAN_HANDOFF
                        ),
                        circuit_open=True,
                        handoff_reason=(
                            None
                            if account.status is AccountStatus.LOGGED_OUT
                            else AccountHandoffReason.DEVICE_EMERGENCY_STOPPED
                        ),
                        session_revision=account.session_revision + 1,
                        updated_at=now,
                    )
                return stopped

            return self._commit_with_audit(
                actor,
                "social.device.emergency_stopped",
                device_id,
                mutate,
                reason=reason.value,
            )

    def bind_account(
        self,
        actor: ActorContext,
        *,
        account_id: UUID,
        platform: SocialPlatform,
        display_name: str,
        device_id: UUID,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.manage")
        self._validate_text(display_name, "display name")
        with self._lock:
            device = self._owned_device(actor, device_id)
            if account_id in self._accounts:
                raise ConflictError("account identifier already exists")
            account = PlatformAccount(
                account_id=account_id,
                tenant_id=actor.tenant_id,
                owner_user_id=actor.user_id,
                device_id=device.device_id,
                platform=platform,
                display_name=display_name,
                status=AccountStatus.AWAITING_SCAN,
                circuit_open=True,
                handoff_reason=None,
                session_revision=0,
                updated_at=self._clock(),
            )

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = account
                return account

            return self._commit_with_audit(
                actor,
                "social.account.bound",
                account_id,
                mutate,
                platform=platform.value,
            )

    def report_account_health(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        signal: AccountHealthSignal,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.execute")
        with self._lock:
            account = self._owned_account(actor, account_id)
            self._require_device_online(actor, account.device_id)
            if (
                account.status is AccountStatus.HUMAN_HANDOFF
                and signal is AccountHealthSignal.AUTHENTICATED
            ):
                raise AuthorizationError("human handoff requires explicit operator resume")
            if signal is AccountHealthSignal.AUTHENTICATED:
                updated = replace(
                    account,
                    status=AccountStatus.HEALTHY,
                    circuit_open=False,
                    handoff_reason=None,
                    updated_at=self._clock(),
                )
                action = "social.account.health_changed"
            else:
                updated = replace(
                    account,
                    status=AccountStatus.HUMAN_HANDOFF,
                    circuit_open=True,
                    handoff_reason=AccountHandoffReason(signal.value),
                    updated_at=self._clock(),
                )
                action = "social.account.handoff_requested"

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = updated
                return updated

            return self._commit_with_audit(
                actor, action, account_id, mutate, signal=signal.value
            )

    def resume_account_after_handoff(
        self,
        actor: ActorContext,
        account_id: UUID,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.manage")
        with self._lock:
            account = self._owned_account(actor, account_id)
            self._require_device_online(actor, account.device_id)
            if account.status is not AccountStatus.HUMAN_HANDOFF:
                raise ConflictError("account is not waiting for human handoff")
            updated = replace(
                account,
                status=AccountStatus.AWAITING_SCAN,
                circuit_open=True,
                handoff_reason=None,
                session_revision=account.session_revision + 1,
                updated_at=self._clock(),
            )

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = updated
                return updated

            return self._commit_with_audit(
                actor, "social.account.handoff_resumed", account_id, mutate
            )

    def require_account_executable(
        self,
        actor: ActorContext,
        account_id: UUID,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.execute")
        with self._lock:
            account = self._owned_account(actor, account_id)
            self._require_device_online(actor, account.device_id)
            if account.circuit_open or account.status is not AccountStatus.HEALTHY:
                raise AuthorizationError("account circuit is open")
            return account

    def get_account(self, actor: ActorContext, account_id: UUID) -> PlatformAccount:
        self._require_permission(actor, "social.read")
        with self._lock:
            return self._owned_account(actor, account_id)

    def list_accounts(self, actor: ActorContext) -> tuple[PlatformAccount, ...]:
        self._require_permission(actor, "social.read")
        with self._lock:
            return tuple(
                account
                for account in sorted(
                    self._accounts.values(), key=lambda item: item.account_id.int
                )
                if account.tenant_id == actor.tenant_id
                and account.owner_user_id == actor.user_id
            )

    def logout_account(self, actor: ActorContext, account_id: UUID) -> PlatformAccount:
        self._require_permission(actor, "social.manage")
        with self._lock:
            account = self._owned_account(actor, account_id)
            updated = replace(
                account,
                status=AccountStatus.LOGGED_OUT,
                circuit_open=True,
                handoff_reason=None,
                session_revision=account.session_revision + 1,
                updated_at=self._clock(),
            )

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = updated
                return updated

            return self._commit_with_audit(
                actor, "social.account.logged_out", account_id, mutate
            )

    def _persist_state(self) -> None:
        if self._state_store is None:
            return
        self._state_revision = self._state_store.save(
            {
                "devices": [
                    {
                        "device_id": str(device.device_id),
                        "tenant_id": str(device.tenant_id),
                        "owner_user_id": str(device.owner_user_id),
                        "display_name": device.display_name,
                        "platform": device.platform.value,
                        "app_version": device.app_version,
                        "executor_version": device.executor_version,
                        "registered_at": device.registered_at.isoformat(),
                        "last_seen_at": device.last_seen_at.isoformat(),
                        "status": device.status.value,
                        "heartbeat_sequence": device.heartbeat_sequence,
                    }
                    for device in self._devices.values()
                ],
                "tasks": [
                    {
                        "task_id": str(task.task_id),
                        "tenant_id": str(task.tenant_id),
                        "requested_by_user_id": str(task.requested_by_user_id),
                        "target_device_id": str(task.target_device_id),
                        "task_type": task.task_type,
                        "status": task.status.value,
                        "created_at": task.created_at.isoformat(),
                        "claimed_at": self._optional_datetime(task.claimed_at),
                        "lease_expires_at": self._optional_datetime(task.lease_expires_at),
                        "claim_attempt": task.claim_attempt,
                    }
                    for task in self._tasks.values()
                ],
                "accounts": [
                    {
                        "account_id": str(account.account_id),
                        "tenant_id": str(account.tenant_id),
                        "owner_user_id": str(account.owner_user_id),
                        "device_id": str(account.device_id),
                        "platform": account.platform.value,
                        "display_name": account.display_name,
                        "status": account.status.value,
                        "circuit_open": account.circuit_open,
                        "handoff_reason": account.handoff_reason,
                        "session_revision": account.session_revision,
                        "updated_at": account.updated_at.isoformat(),
                    }
                    for account in self._accounts.values()
                ],
                "audit_outbox": [
                    {
                        "event_id": str(event.event_id),
                        "action": event.action,
                        "tenant_id": str(event.tenant_id),
                        "actor_user_id": str(event.actor_user_id),
                        "resource_id": str(event.resource_id),
                        "occurred_at": event.occurred_at.isoformat(),
                        "details": [list(detail) for detail in event.details],
                    }
                    for event in self._audit_outbox
                ],
            },
            expected_revision=self._state_revision,
        )

    def _restore_state(self, state: Mapping[str, Any] | None) -> None:
        if state is None:
            return
        for raw in self._records(state, "devices"):
            device = Device(
                device_id=UUID(str(raw["device_id"])),
                tenant_id=UUID(str(raw["tenant_id"])),
                owner_user_id=UUID(str(raw["owner_user_id"])),
                display_name=str(raw["display_name"]),
                platform=DevicePlatform(str(raw["platform"])),
                app_version=str(raw["app_version"]),
                executor_version=str(raw["executor_version"]),
                registered_at=datetime.fromisoformat(str(raw["registered_at"])),
                last_seen_at=datetime.fromisoformat(str(raw["last_seen_at"])),
                status=DeviceStatus(str(raw["status"])),
                heartbeat_sequence=int(raw["heartbeat_sequence"]),
            )
            self._devices[device.device_id] = device
        for raw in self._records(state, "tasks"):
            task = LocalTask(
                task_id=UUID(str(raw["task_id"])),
                tenant_id=UUID(str(raw["tenant_id"])),
                requested_by_user_id=UUID(str(raw["requested_by_user_id"])),
                target_device_id=UUID(str(raw["target_device_id"])),
                task_type=str(raw["task_type"]),
                status=LocalTaskStatus(str(raw["status"])),
                created_at=datetime.fromisoformat(str(raw["created_at"])),
                claimed_at=self._parse_optional_datetime(raw.get("claimed_at")),
                lease_expires_at=self._parse_optional_datetime(raw.get("lease_expires_at")),
                claim_attempt=int(raw["claim_attempt"]),
            )
            self._tasks[task.task_id] = task
        for raw in self._records(state, "accounts"):
            account = PlatformAccount(
                account_id=UUID(str(raw["account_id"])),
                tenant_id=UUID(str(raw["tenant_id"])),
                owner_user_id=UUID(str(raw["owner_user_id"])),
                device_id=UUID(str(raw["device_id"])),
                platform=SocialPlatform(str(raw["platform"])),
                display_name=str(raw["display_name"]),
                status=AccountStatus(str(raw["status"])),
                circuit_open=bool(raw["circuit_open"]),
                handoff_reason=(
                    AccountHandoffReason(str(raw["handoff_reason"]))
                    if raw.get("handoff_reason") is not None
                    else None
                ),
                session_revision=int(raw["session_revision"]),
                updated_at=datetime.fromisoformat(str(raw["updated_at"])),
            )
            self._accounts[account.account_id] = account
        for raw in self._records(state, "audit_outbox"):
            raw_details = raw.get("details", ())
            if not isinstance(raw_details, list | tuple):
                raise ValueError("invalid persisted audit outbox")
            details = tuple(
                (str(detail[0]), str(detail[1]))
                for detail in raw_details
                if isinstance(detail, list | tuple) and len(detail) == 2
            )
            if len(details) != len(raw_details):
                raise ValueError("invalid persisted audit outbox")
            self._audit_outbox.append(
                AuditEvent(
                    event_id=UUID(str(raw["event_id"])),
                    action=str(raw["action"]),
                    tenant_id=UUID(str(raw["tenant_id"])),
                    actor_user_id=UUID(str(raw["actor_user_id"])),
                    resource_id=UUID(str(raw["resource_id"])),
                    occurred_at=datetime.fromisoformat(str(raw["occurred_at"])),
                    details=details,
                )
            )

    @staticmethod
    def _records(state: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
        value = state.get(key, ())
        if not isinstance(value, list | tuple) or any(
            not isinstance(record, Mapping) for record in value
        ):
            raise ValueError("invalid persisted Social Operations state")
        return tuple(value)

    @staticmethod
    def _optional_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_optional_datetime(value: object) -> datetime | None:
        return datetime.fromisoformat(str(value)) if value is not None else None

    @staticmethod
    def _require_permission(actor: ActorContext, permission: str) -> None:
        if permission not in actor.permissions:
            raise AuthorizationError(f"missing permission: {permission}")

    @staticmethod
    def _validate_text(value: str, field: str) -> None:
        if not value or value != value.strip() or len(value) > 128:
            raise ValueError(f"invalid {field}")

    def _tenant_device(self, actor: ActorContext, device_id: UUID) -> Device:
        device = self._devices.get(device_id)
        if device is None or device.tenant_id != actor.tenant_id:
            raise ResourceNotFoundError("device not found")
        return device

    def _device_with_online_state(self, device: Device) -> Device:
        if (
            device.status is not DeviceStatus.EMERGENCY_STOPPED
            and self._clock() - device.last_seen_at > self._offline_after
        ):
            return replace(device, status=DeviceStatus.OFFLINE)
        return device

    def _owned_device(self, actor: ActorContext, device_id: UUID) -> Device:
        device = self._tenant_device(actor, device_id)
        if device.owner_user_id != actor.user_id:
            raise ResourceNotFoundError("device not found")
        return device

    def _tenant_account(self, actor: ActorContext, account_id: UUID) -> PlatformAccount:
        account = self._accounts.get(account_id)
        if account is None or account.tenant_id != actor.tenant_id:
            raise ResourceNotFoundError("account not found")
        return account

    def _owned_account(self, actor: ActorContext, account_id: UUID) -> PlatformAccount:
        account = self._tenant_account(actor, account_id)
        if account.owner_user_id != actor.user_id:
            raise ResourceNotFoundError("account not found")
        return account

    def _require_device_online(self, actor: ActorContext, device_id: UUID) -> Device:
        device = self._device_with_online_state(self._owned_device(actor, device_id))
        if device.status is not DeviceStatus.ONLINE:
            raise AuthorizationError("device is not online")
        return device

    def _replace_device(self, device_id: UUID, device: Device) -> Device:
        self._devices[device_id] = device
        return device

    def _commit_state(self, mutation: Callable[[], _ResultT]) -> _ResultT:
        devices_before = self._devices.copy()
        tasks_before = self._tasks.copy()
        accounts_before = self._accounts.copy()
        outbox_before = self._audit_outbox.copy()
        try:
            result = mutation()
            self._persist_state()
        except Exception:
            self._devices = devices_before
            self._tasks = tasks_before
            self._accounts = accounts_before
            self._audit_outbox = outbox_before
            raise
        return result

    def _commit_with_audit(
        self,
        actor: ActorContext,
        action: str,
        resource_id: UUID,
        mutation: Callable[[], _ResultT],
        **details: str,
    ) -> _ResultT:
        event = AuditEvent(
            event_id=uuid4(),
            action=action,
            tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            resource_id=resource_id,
            occurred_at=self._clock(),
            details=tuple(sorted(details.items())),
        )

        def mutate_and_enqueue() -> _ResultT:
            result = mutation()
            self._audit_outbox.append(event)
            return result

        devices_before = self._devices.copy()
        tasks_before = self._tasks.copy()
        accounts_before = self._accounts.copy()
        outbox_before = self._audit_outbox.copy()
        try:
            result = mutate_and_enqueue()
            self._persist_state()
            if self._state_store is None:
                self._audit_sink.record(event)
                self._audit_outbox.pop()
                return result
        except Exception:
            self._devices = devices_before
            self._tasks = tasks_before
            self._accounts = accounts_before
            self._audit_outbox = outbox_before
            raise
        self._drain_audit_outbox()
        return result

    def _drain_audit_outbox(self) -> None:
        if self._state_store is None:
            return
        while self._audit_outbox:
            event = self._audit_outbox[0]
            try:
                self._audit_sink.record(event)
            except Exception:
                return
            self._audit_outbox.pop(0)
            try:
                self._persist_state()
            except Exception:
                self._audit_outbox.insert(0, event)
                return
