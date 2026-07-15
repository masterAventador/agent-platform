from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
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
    PAUSED = "paused"


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
    CONSECUTIVE_FAILURES = "consecutive_failures"
    ABNORMAL_BEHAVIOR = "abnormal_behavior"
    REMOTE_STOP = "remote_stop"


class AccountActionResult(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABNORMAL_BEHAVIOR = "abnormal_behavior"


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
    created_at: datetime
    session_revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AccountGovernancePolicy:
    platform: SocialPlatform
    action_type: str
    min_interval: timedelta
    daily_limit: int
    cold_start_daily_limit: int
    cold_start_days: int
    consecutive_failure_threshold: int


@dataclass(frozen=True, slots=True)
class AccountActionState:
    account_id: UUID
    action_type: str
    daily_window: date
    daily_count: int
    last_authorized_at: datetime | None
    consecutive_failures: int


@dataclass(frozen=True, slots=True)
class AccountActionAuthorization:
    account_id: UUID
    action_type: str
    allowed: bool
    remaining_daily: int
    next_available_at: datetime | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AccountActionRecord:
    account_id: UUID
    action_type: str
    idempotency_key: str
    result: AccountActionResult
    occurred_at: datetime
    consecutive_failures: int


@dataclass(frozen=True, slots=True)
class AccountPolicyLimit:
    action_type: str
    daily_limit: int
    effective_daily_limit: int
    remaining_daily: int
    min_interval_seconds: int
    cold_start_days: int
    consecutive_failure_threshold: int
    next_available_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountGovernanceSnapshot:
    account_id: UUID
    status: AccountStatus
    circuit_open: bool
    health_score: int
    recent_tasks: tuple[AccountActionRecord, ...]
    failure_trend: Mapping[str, int]
    policy_limits: Mapping[str, AccountPolicyLimit]
    recommendations: tuple[str, ...]


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
        self._policies: dict[tuple[SocialPlatform, str], AccountGovernancePolicy] = {}
        self._action_states: dict[tuple[UUID, str], AccountActionState] = {}
        self._action_records: list[AccountActionRecord] = []
        self._action_authorizations: dict[str, AccountActionAuthorization] = {}
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
            now = self._clock()
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
                created_at=now,
                session_revision=0,
                updated_at=now,
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
            if account.status is AccountStatus.PAUSED:
                raise AuthorizationError("paused account requires explicit operator resume")
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

    def configure_account_governance_policy(
        self,
        actor: ActorContext,
        *,
        platform: SocialPlatform,
        action_type: str,
        min_interval: timedelta,
        daily_limit: int,
        cold_start_daily_limit: int,
        cold_start_days: int,
        consecutive_failure_threshold: int,
    ) -> AccountGovernancePolicy:
        self._require_permission(actor, "social.manage")
        action_type = self._validate_action_type(action_type)
        if min_interval < timedelta(0):
            raise ValueError("minimum interval must be non-negative")
        if daily_limit < 1:
            raise ValueError("daily limit must be positive")
        if cold_start_daily_limit < 1:
            raise ValueError("cold start daily limit must be positive")
        if cold_start_daily_limit > daily_limit:
            raise ValueError("cold start daily limit cannot exceed daily limit")
        if cold_start_days < 0:
            raise ValueError("cold start days must be non-negative")
        if consecutive_failure_threshold < 1:
            raise ValueError("consecutive failure threshold must be positive")
        policy = AccountGovernancePolicy(
            platform=platform,
            action_type=action_type,
            min_interval=min_interval,
            daily_limit=daily_limit,
            cold_start_daily_limit=cold_start_daily_limit,
            cold_start_days=cold_start_days,
            consecutive_failure_threshold=consecutive_failure_threshold,
        )

        def mutate() -> AccountGovernancePolicy:
            self._policies[(platform, action_type)] = policy
            return policy

        return self._commit_with_audit(
            actor,
            "social.account_governance.policy_configured",
            actor.tenant_id,
            mutate,
            platform=platform.value,
            action_type=action_type,
        )

    def authorize_account_action(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        action_type: str,
        idempotency_key: str,
    ) -> AccountActionAuthorization:
        self._require_permission(actor, "social.execute")
        action_type = self._validate_action_type(action_type)
        self._validate_text(idempotency_key, "idempotency key")
        with self._lock:
            account = self._owned_account(actor, account_id)
            self._require_device_online(actor, account.device_id)
            if account.status is AccountStatus.PAUSED:
                self._audit_denied_action(actor, account, action_type, "paused")
                raise AuthorizationError("account is paused")
            if account.circuit_open or account.status is not AccountStatus.HEALTHY:
                self._audit_denied_action(actor, account, action_type, "circuit_open")
                raise AuthorizationError("account circuit is open")
            authorization_key = self._authorization_key(
                account_id, action_type, idempotency_key
            )
            existing = self._action_authorizations.get(authorization_key)
            if existing is not None:
                return existing
            now = self._clock()
            policy = self._policy_for(account.platform, action_type)
            state = self._fresh_action_state(account_id, action_type, now)
            if (
                state.last_authorized_at is not None
                and now < state.last_authorized_at + policy.min_interval
            ):
                self._audit_denied_action(actor, account, action_type, "frequency")
                raise AuthorizationError("frequency limit exceeded")
            effective_daily_limit = self._effective_daily_limit(account, policy, now)
            if state.daily_count >= effective_daily_limit:
                self._audit_denied_action(actor, account, action_type, "daily_limit")
                raise AuthorizationError("daily limit exceeded")
            updated_state = replace(
                state,
                daily_count=state.daily_count + 1,
                last_authorized_at=now,
            )
            decision = AccountActionAuthorization(
                account_id=account_id,
                action_type=action_type,
                allowed=True,
                remaining_daily=effective_daily_limit - updated_state.daily_count,
                next_available_at=now + policy.min_interval
                if policy.min_interval > timedelta(0)
                else None,
                idempotency_key=idempotency_key,
            )

            def mutate() -> AccountActionAuthorization:
                self._action_states[(account_id, action_type)] = updated_state
                self._action_authorizations[authorization_key] = decision
                return decision

            return self._commit_with_audit(
                actor,
                "social.account.action_authorized",
                account_id,
                mutate,
                action_type=action_type,
            )

    def record_account_action_result(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        action_type: str,
        result: AccountActionResult,
        idempotency_key: str,
        failure_reason: str | None = None,
    ) -> AccountGovernanceSnapshot:
        self._require_permission(actor, "social.execute")
        action_type = self._validate_action_type(action_type)
        self._validate_text(idempotency_key, "idempotency key")
        if not isinstance(result, AccountActionResult):
            raise ValueError("invalid account action result")
        with self._lock:
            account = self._owned_account(actor, account_id)
            existing_record = next(
                (
                    record
                    for record in self._action_records
                    if record.account_id == account_id
                    and record.action_type == action_type
                    and record.idempotency_key == idempotency_key
                ),
                None,
            )
            if existing_record is not None:
                if existing_record.result is not result:
                    raise ConflictError("action result already recorded")
                return self._account_governance_snapshot(account)
            authorization_key = self._authorization_key(
                account_id, action_type, idempotency_key
            )
            if authorization_key not in self._action_authorizations:
                self._audit_denied_action(actor, account, action_type, "not_authorized")
                raise AuthorizationError("action result was not authorized")
            now = self._clock()
            state = self._fresh_action_state(account_id, action_type, now)
            if result is AccountActionResult.SUCCEEDED:
                updated_state = replace(state, consecutive_failures=0)
                updated_account = account
                action = "social.account.action_result_recorded"
            else:
                failures = state.consecutive_failures + 1
                updated_state = replace(state, consecutive_failures=failures)
                policy = self._policy_for(account.platform, action_type)
                handoff_reason = (
                    AccountHandoffReason.ABNORMAL_BEHAVIOR
                    if result is AccountActionResult.ABNORMAL_BEHAVIOR
                    else AccountHandoffReason.CONSECUTIVE_FAILURES
                )
                if (
                    result is AccountActionResult.ABNORMAL_BEHAVIOR
                    or failures >= policy.consecutive_failure_threshold
                ):
                    updated_account = replace(
                        account,
                        status=AccountStatus.HUMAN_HANDOFF,
                        circuit_open=True,
                        handoff_reason=handoff_reason,
                        updated_at=now,
                    )
                    action = "social.account.circuit_opened"
                else:
                    updated_account = account
                    action = "social.account.action_result_recorded"
            record = AccountActionRecord(
                account_id=account_id,
                action_type=action_type,
                idempotency_key=idempotency_key,
                result=result,
                occurred_at=now,
                consecutive_failures=updated_state.consecutive_failures,
            )

            def mutate() -> AccountGovernanceSnapshot:
                self._action_states[(account_id, action_type)] = updated_state
                self._accounts[account_id] = updated_account
                self._action_records.append(record)
                return self._account_governance_snapshot(updated_account)

            return self._commit_with_audit(
                actor,
                action,
                account_id,
                mutate,
                action_type=action_type,
                result=result.value,
            )

    def pause_account(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        reason: str,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.manage")
        self._validate_text(reason, "pause reason")
        with self._lock:
            account = self._owned_account(actor, account_id)
            if account.status is AccountStatus.LOGGED_OUT:
                raise ConflictError("logged out account cannot be paused")
            updated = replace(
                account,
                status=AccountStatus.PAUSED,
                circuit_open=True,
                handoff_reason=None,
                session_revision=account.session_revision + 1,
                updated_at=self._clock(),
            )

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = updated
                return updated

            return self._commit_with_audit(
                actor, "social.account.paused", account_id, mutate
            )

    def resume_account(
        self,
        actor: ActorContext,
        account_id: UUID,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.manage")
        with self._lock:
            account = self._owned_account(actor, account_id)
            self._require_device_online(actor, account.device_id)
            if account.status is AccountStatus.PAUSED:
                updated = replace(
                    account,
                    status=AccountStatus.HEALTHY,
                    circuit_open=False,
                    handoff_reason=None,
                    session_revision=account.session_revision + 1,
                    updated_at=self._clock(),
                )
            elif account.status is AccountStatus.HUMAN_HANDOFF:
                updated = replace(
                    account,
                    status=AccountStatus.AWAITING_SCAN,
                    circuit_open=True,
                    handoff_reason=None,
                    session_revision=account.session_revision + 1,
                    updated_at=self._clock(),
                )
            else:
                raise ConflictError("account is not paused or waiting for human handoff")

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = updated
                return updated

            return self._commit_with_audit(
                actor, "social.account.resumed", account_id, mutate
            )

    def remote_stop_account(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        reason: str,
    ) -> PlatformAccount:
        self._require_permission(actor, "social.manage")
        self._validate_text(reason, "remote stop reason")
        with self._lock:
            account = self._owned_account(actor, account_id)
            updated = replace(
                account,
                status=AccountStatus.HUMAN_HANDOFF,
                circuit_open=True,
                handoff_reason=AccountHandoffReason.REMOTE_STOP,
                session_revision=account.session_revision + 1,
                updated_at=self._clock(),
            )

            def mutate() -> PlatformAccount:
                self._accounts[account_id] = updated
                return updated

            return self._commit_with_audit(
                actor, "social.account.remote_stopped", account_id, mutate
            )

    def get_account_governance(
        self,
        actor: ActorContext,
        account_id: UUID,
    ) -> AccountGovernanceSnapshot:
        self._require_permission(actor, "social.read")
        with self._lock:
            account = self._owned_account(actor, account_id)
            return self._account_governance_snapshot(account)

    def resume_account_after_handoff(
        self,
        actor: ActorContext,
        account_id: UUID,
    ) -> PlatformAccount:
        return self.resume_account(actor, account_id)

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

    def _audit_denied_action(
        self,
        actor: ActorContext,
        account: PlatformAccount,
        action_type: str,
        reason: str,
    ) -> None:
        self._commit_with_audit(
            actor,
            "social.account.action_denied",
            account.account_id,
            lambda: None,
            action_type=action_type,
            reason=reason,
        )

    @staticmethod
    def _authorization_key(
        account_id: UUID,
        action_type: str,
        idempotency_key: str,
    ) -> str:
        return f"{account_id}:{action_type}:{idempotency_key}"

    def _policy_for(
        self,
        platform: SocialPlatform,
        action_type: str,
    ) -> AccountGovernancePolicy:
        policy = self._policies.get((platform, action_type))
        if policy is not None:
            return policy
        return AccountGovernancePolicy(
            platform=platform,
            action_type=action_type,
            min_interval=timedelta(seconds=60),
            daily_limit=20,
            cold_start_daily_limit=5,
            cold_start_days=7,
            consecutive_failure_threshold=3,
        )

    def _fresh_action_state(
        self,
        account_id: UUID,
        action_type: str,
        now: datetime,
    ) -> AccountActionState:
        state = self._action_states.get((account_id, action_type))
        today = now.date()
        if state is None:
            return AccountActionState(
                account_id=account_id,
                action_type=action_type,
                daily_window=today,
                daily_count=0,
                last_authorized_at=None,
                consecutive_failures=0,
            )
        if state.daily_window != today:
            return replace(
                state,
                daily_window=today,
                daily_count=0,
                last_authorized_at=None,
            )
        return state

    @staticmethod
    def _effective_daily_limit(
        account: PlatformAccount,
        policy: AccountGovernancePolicy,
        now: datetime,
    ) -> int:
        cold_start_until = account.created_at + timedelta(days=policy.cold_start_days)
        if policy.cold_start_days > 0 and now < cold_start_until:
            return policy.cold_start_daily_limit
        return policy.daily_limit

    def _account_governance_snapshot(
        self,
        account: PlatformAccount,
    ) -> AccountGovernanceSnapshot:
        now = self._clock()
        recent_tasks = tuple(
            record
            for record in reversed(self._action_records)
            if record.account_id == account.account_id
        )[:10]
        failure_trend: dict[str, int] = {}
        for record in recent_tasks:
            if record.result in {
                AccountActionResult.FAILED,
                AccountActionResult.ABNORMAL_BEHAVIOR,
            }:
                failure_trend[record.action_type] = (
                    failure_trend.get(record.action_type, 0) + 1
                )
        policy_limits = {
            policy.action_type: self._policy_limit_for(account, policy, now)
            for (platform, _), policy in sorted(
                self._policies.items(), key=lambda item: item[0][1]
            )
            if platform == account.platform
        }
        recommendations = self._recommendations_for(
            account,
            policy_limits=policy_limits,
            failure_trend=failure_trend,
            now=now,
        )
        health_score = max(0, 100 - min(sum(failure_trend.values()), 5) * 20)
        return AccountGovernanceSnapshot(
            account_id=account.account_id,
            status=account.status,
            circuit_open=account.circuit_open,
            health_score=health_score,
            recent_tasks=recent_tasks,
            failure_trend=failure_trend,
            policy_limits=policy_limits,
            recommendations=recommendations,
        )

    def _policy_limit_for(
        self,
        account: PlatformAccount,
        policy: AccountGovernancePolicy,
        now: datetime,
    ) -> AccountPolicyLimit:
        state = self._fresh_action_state(account.account_id, policy.action_type, now)
        effective_daily_limit = self._effective_daily_limit(account, policy, now)
        next_available_at = (
            state.last_authorized_at + policy.min_interval
            if state.last_authorized_at is not None
            and policy.min_interval > timedelta(0)
            else None
        )
        return AccountPolicyLimit(
            action_type=policy.action_type,
            daily_limit=policy.daily_limit,
            effective_daily_limit=effective_daily_limit,
            remaining_daily=max(0, effective_daily_limit - state.daily_count),
            min_interval_seconds=int(policy.min_interval.total_seconds()),
            cold_start_days=policy.cold_start_days,
            consecutive_failure_threshold=policy.consecutive_failure_threshold,
            next_available_at=next_available_at,
        )

    def _recommendations_for(
        self,
        account: PlatformAccount,
        *,
        policy_limits: Mapping[str, AccountPolicyLimit],
        failure_trend: Mapping[str, int],
        now: datetime,
    ) -> tuple[str, ...]:
        recommendations: list[str] = []
        if account.status is AccountStatus.PAUSED:
            recommendations.append("账号已暂停，请完成复核后再恢复自动执行。")
        if account.handoff_reason is AccountHandoffReason.CONSECUTIVE_FAILURES:
            recommendations.append("连续失败已触发熔断，请人工检查平台页面和任务参数。")
        elif account.handoff_reason is AccountHandoffReason.ABNORMAL_BEHAVIOR:
            recommendations.append("检测到异常行为，请人工复核账号安全状态。")
        elif account.handoff_reason in {
            AccountHandoffReason.CAPTCHA_REQUIRED,
            AccountHandoffReason.RISK_CONTROL,
            AccountHandoffReason.LOGIN_EXPIRED,
        }:
            recommendations.append("平台要求人工处理登录、验证码或风控提示。")
        for action_type, limit in policy_limits.items():
            if limit.remaining_daily > 0:
                continue
            if limit.effective_daily_limit < limit.daily_limit and (
                account.created_at
                + timedelta(days=self._policy_for(account.platform, action_type).cold_start_days)
            ) > now:
                recommendations.append(
                    f"冷启动账号今日已达到 {action_type} 上限，请明日再执行。"
                )
            else:
                recommendations.append(
                    f"账号今日已达到 {action_type} 每日上限，请明日再执行。"
                )
        if not recommendations and failure_trend:
            recommendations.append("近期存在失败任务，请关注平台状态和内容参数。")
        return tuple(recommendations)

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
                        "created_at": account.created_at.isoformat(),
                        "session_revision": account.session_revision,
                        "updated_at": account.updated_at.isoformat(),
                    }
                    for account in self._accounts.values()
                ],
                "policies": [
                    {
                        "platform": policy.platform.value,
                        "action_type": policy.action_type,
                        "min_interval_seconds": int(policy.min_interval.total_seconds()),
                        "daily_limit": policy.daily_limit,
                        "cold_start_daily_limit": policy.cold_start_daily_limit,
                        "cold_start_days": policy.cold_start_days,
                        "consecutive_failure_threshold": (
                            policy.consecutive_failure_threshold
                        ),
                    }
                    for policy in self._policies.values()
                ],
                "action_states": [
                    {
                        "account_id": str(state.account_id),
                        "action_type": state.action_type,
                        "daily_window": state.daily_window.isoformat(),
                        "daily_count": state.daily_count,
                        "last_authorized_at": self._optional_datetime(
                            state.last_authorized_at
                        ),
                        "consecutive_failures": state.consecutive_failures,
                    }
                    for state in self._action_states.values()
                ],
                "action_records": [
                    {
                        "account_id": str(record.account_id),
                        "action_type": record.action_type,
                        "idempotency_key": record.idempotency_key,
                        "result": record.result.value,
                        "occurred_at": record.occurred_at.isoformat(),
                        "consecutive_failures": record.consecutive_failures,
                    }
                    for record in self._action_records
                ],
                "action_authorizations": [
                    {
                        "authorization_key": key,
                        "account_id": str(authorization.account_id),
                        "action_type": authorization.action_type,
                        "allowed": authorization.allowed,
                        "remaining_daily": authorization.remaining_daily,
                        "next_available_at": self._optional_datetime(
                            authorization.next_available_at
                        ),
                        "idempotency_key": authorization.idempotency_key,
                    }
                    for key, authorization in self._action_authorizations.items()
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
                created_at=datetime.fromisoformat(
                    str(raw.get("created_at", raw["updated_at"]))
                ),
                session_revision=int(raw["session_revision"]),
                updated_at=datetime.fromisoformat(str(raw["updated_at"])),
            )
            self._accounts[account.account_id] = account
        for raw in self._records(state, "policies"):
            policy = AccountGovernancePolicy(
                platform=SocialPlatform(str(raw["platform"])),
                action_type=str(raw["action_type"]),
                min_interval=timedelta(seconds=int(raw["min_interval_seconds"])),
                daily_limit=int(raw["daily_limit"]),
                cold_start_daily_limit=int(raw["cold_start_daily_limit"]),
                cold_start_days=int(raw["cold_start_days"]),
                consecutive_failure_threshold=int(
                    raw["consecutive_failure_threshold"]
                ),
            )
            self._policies[(policy.platform, policy.action_type)] = policy
        for raw in self._records(state, "action_states"):
            action_state = AccountActionState(
                account_id=UUID(str(raw["account_id"])),
                action_type=str(raw["action_type"]),
                daily_window=date.fromisoformat(str(raw["daily_window"])),
                daily_count=int(raw["daily_count"]),
                last_authorized_at=self._parse_optional_datetime(
                    raw.get("last_authorized_at")
                ),
                consecutive_failures=int(raw["consecutive_failures"]),
            )
            self._action_states[
                (action_state.account_id, action_state.action_type)
            ] = action_state
        for raw in self._records(state, "action_records"):
            self._action_records.append(
                AccountActionRecord(
                    account_id=UUID(str(raw["account_id"])),
                    action_type=str(raw["action_type"]),
                    idempotency_key=str(
                        raw.get("idempotency_key")
                        or f"legacy:{len(self._action_records)}"
                    ),
                    result=AccountActionResult(str(raw["result"])),
                    occurred_at=datetime.fromisoformat(str(raw["occurred_at"])),
                    consecutive_failures=int(raw["consecutive_failures"]),
                )
            )
        for raw in self._records(state, "action_authorizations"):
            authorization = AccountActionAuthorization(
                account_id=UUID(str(raw["account_id"])),
                action_type=str(raw["action_type"]),
                allowed=bool(raw["allowed"]),
                remaining_daily=int(raw["remaining_daily"]),
                next_available_at=self._parse_optional_datetime(
                    raw.get("next_available_at")
                ),
                idempotency_key=str(raw["idempotency_key"]),
            )
            self._action_authorizations[str(raw["authorization_key"])] = authorization
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

    @staticmethod
    def _validate_action_type(value: str) -> str:
        if (
            not value
            or value != value.strip()
            or len(value) > 128
            or not value.replace("_", "").replace(".", "").isalnum()
        ):
            raise ValueError("invalid action type")
        return value

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
        policies_before = self._policies.copy()
        action_states_before = self._action_states.copy()
        action_records_before = self._action_records.copy()
        action_authorizations_before = self._action_authorizations.copy()
        outbox_before = self._audit_outbox.copy()
        try:
            result = mutation()
            self._persist_state()
        except Exception:
            self._devices = devices_before
            self._tasks = tasks_before
            self._accounts = accounts_before
            self._policies = policies_before
            self._action_states = action_states_before
            self._action_records = action_records_before
            self._action_authorizations = action_authorizations_before
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
        policies_before = self._policies.copy()
        action_states_before = self._action_states.copy()
        action_records_before = self._action_records.copy()
        action_authorizations_before = self._action_authorizations.copy()
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
            self._policies = policies_before
            self._action_states = action_states_before
            self._action_records = action_records_before
            self._action_authorizations = action_authorizations_before
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
