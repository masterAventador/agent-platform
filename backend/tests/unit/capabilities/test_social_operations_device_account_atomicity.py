from __future__ import annotations

import shutil
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID

import pytest

from agent_platform.capabilities.social_operations import device_account_persistence
from agent_platform.capabilities.social_operations.device_account_persistence import (
    SqliteDeviceAccountStateStore,
)
from agent_platform.capabilities.social_operations.device_account_service import (
    AccountHealthSignal,
    AccountStatus,
    ActorContext,
    AuditEvent,
    AuthorizationError,
    ConflictError,
    DeviceAccountService,
    DevicePlatform,
    DeviceStatus,
    EmergencyStopReason,
    InMemoryAuditSink,
    LocalTaskStatus,
    ResourceNotFoundError,
    SocialPlatform,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000101")
OWNER_ID = UUID("00000000-0000-4000-8000-000000000201")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000202")
DEVICE_ID = UUID("00000000-0000-4000-8000-000000000301")
TASK_ID = UUID("00000000-0000-4000-8000-000000000401")
ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000501")


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class MemoryStore:
    def __init__(self) -> None:
        self.state: Mapping[str, Any] | None = None
        self.revision = 0
        self.fail_next_save = False

    def load(self) -> tuple[int, Mapping[str, Any]] | None:
        if self.state is None:
            return None
        return self.revision, deepcopy(self.state)

    def save(self, state: Mapping[str, Any], *, expected_revision: int) -> int:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("storage unavailable")
        if expected_revision != self.revision:
            raise ConflictError("persisted state revision changed")
        self.revision += 1
        self.state = deepcopy(state)
        return self.revision


class FailingAuditSink:
    def record(self, event: AuditEvent) -> None:
        raise OSError("audit unavailable")


def actor(
    user_id: UUID = OWNER_ID,
    *,
    permissions: frozenset[str] = frozenset(
        {"social.read", "social.execute", "social.manage"}
    ),
) -> ActorContext:
    return ActorContext(tenant_id=TENANT_ID, user_id=user_id, permissions=permissions)


def make_service(
    clock: MutableClock,
    *,
    audit: InMemoryAuditSink | FailingAuditSink | None = None,
    store: MemoryStore | None = None,
) -> DeviceAccountService:
    return DeviceAccountService(
        clock=clock,
        audit_sink=audit or InMemoryAuditSink(),
        offline_after=timedelta(seconds=30),
        claim_lease=timedelta(seconds=10),
        state_store=store,
    )


def register(service: DeviceAccountService) -> None:
    service.register_device(
        actor(),
        device_id=DEVICE_ID,
        display_name="Marketing Mac",
        platform=DevicePlatform.MACOS,
        app_version="0.1.0",
        executor_version="1.0.0",
        heartbeat_sequence=0,
    )


def bind_healthy_account(service: DeviceAccountService) -> None:
    service.bind_account(
        actor(),
        account_id=ACCOUNT_ID,
        platform=SocialPlatform.DOUYIN,
        display_name="Demo Publisher",
        device_id=DEVICE_ID,
    )
    service.report_account_health(
        actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED
    )


def test_claim_vs_stop_is_linearizable_and_stop_wins_final_state() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    service.enqueue_task(
        actor(),
        task_id=TASK_ID,
        target_device_id=DEVICE_ID,
        task_type="social.account.health_check",
    )
    barrier = Barrier(2)

    def claim() -> tuple[object, ...]:
        barrier.wait()
        return service.claim_tasks(actor(), DEVICE_ID, limit=1)

    def stop() -> object:
        barrier.wait()
        return service.emergency_stop(
            actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda operation: operation(), (claim, stop)))

    assert service.get_device(actor(), DEVICE_ID).status is DeviceStatus.EMERGENCY_STOPPED
    assert service.get_task(actor(), TASK_ID).status is LocalTaskStatus.CANCELLED
    assert service.claim_tasks(actor(), DEVICE_ID, limit=1) == ()


def test_enqueue_vs_stop_never_leaves_runnable_work() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    barrier = Barrier(2)

    def enqueue() -> object:
        barrier.wait()
        try:
            return service.enqueue_task(
                actor(),
                task_id=TASK_ID,
                target_device_id=DEVICE_ID,
                task_type="social.account.health_check",
            )
        except AuthorizationError:
            return None

    def stop() -> object:
        barrier.wait()
        return service.emergency_stop(
            actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda operation: operation(), (enqueue, stop)))

    assert service.get_device(actor(), DEVICE_ID).status is DeviceStatus.EMERGENCY_STOPPED
    try:
        task = service.get_task(actor(), TASK_ID)
    except ResourceNotFoundError:
        pass
    else:
        assert task.status is LocalTaskStatus.CANCELLED


def test_account_execution_vs_stop_circuits_bound_account() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    bind_healthy_account(service)
    barrier = Barrier(2)

    def execute_gate() -> object:
        barrier.wait()
        try:
            return service.require_account_executable(actor(), ACCOUNT_ID)
        except AuthorizationError:
            return None

    def stop() -> object:
        barrier.wait()
        return service.emergency_stop(
            actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda operation: operation(), (execute_gate, stop)))

    account = service.get_account(actor(), ACCOUNT_ID)
    assert account.status is AccountStatus.HUMAN_HANDOFF
    assert account.circuit_open is True
    with pytest.raises(AuthorizationError, match="circuit|device"):
        service.require_account_executable(actor(), ACCOUNT_ID)


def test_reregister_preserves_heartbeat_last_seen_and_effective_status() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    clock.now += timedelta(seconds=1)
    heartbeat = service.heartbeat(
        actor(),
        device_id=DEVICE_ID,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=7,
    )
    clock.now += timedelta(seconds=31)

    replay = service.register_device(
        actor(),
        device_id=DEVICE_ID,
        display_name="Renamed Mac",
        platform=DevicePlatform.MACOS,
        app_version="0.3.0",
        executor_version="1.2.0",
        heartbeat_sequence=0,
    )

    assert replay.heartbeat_sequence == 7
    assert replay.last_seen_at == heartbeat.last_seen_at
    assert replay.status is DeviceStatus.OFFLINE


def test_offline_device_cannot_claim_or_execute_account() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    bind_healthy_account(service)
    service.enqueue_task(
        actor(),
        task_id=TASK_ID,
        target_device_id=DEVICE_ID,
        task_type="social.account.health_check",
    )
    clock.now += timedelta(seconds=31)

    assert service.claim_tasks(actor(), DEVICE_ID, limit=1) == ()
    with pytest.raises(AuthorizationError, match="device is not online"):
        service.require_account_executable(actor(), ACCOUNT_ID)


def test_non_owner_cannot_see_or_bind_device_and_account() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    bind_healthy_account(service)

    with pytest.raises(ResourceNotFoundError):
        service.get_device(actor(OTHER_USER_ID), DEVICE_ID)
    assert service.list_devices(actor(OTHER_USER_ID)) == ()
    with pytest.raises(ResourceNotFoundError):
        service.bind_account(
            actor(OTHER_USER_ID),
            account_id=UUID("00000000-0000-4000-8000-000000000502"),
            platform=SocialPlatform.DOUYIN,
            display_name="Not Mine",
            device_id=DEVICE_ID,
        )
    with pytest.raises(ResourceNotFoundError):
        service.get_account(actor(OTHER_USER_ID), ACCOUNT_ID)
    assert service.list_accounts(actor(OTHER_USER_ID)) == ()


def test_lease_expiring_exactly_now_is_reclaimable() -> None:
    clock = MutableClock()
    service = make_service(clock)
    register(service)
    service.enqueue_task(
        actor(),
        task_id=TASK_ID,
        target_device_id=DEVICE_ID,
        task_type="social.account.health_check",
    )
    assert service.claim_tasks(actor(), DEVICE_ID, limit=1)[0].claim_attempt == 1
    clock.now += timedelta(seconds=10)

    reclaimed = service.claim_tasks(actor(), DEVICE_ID, limit=1)

    assert reclaimed[0].claim_attempt == 2


def test_persistence_failure_rolls_back_in_memory_state() -> None:
    clock = MutableClock()
    store = MemoryStore()
    service = make_service(clock, store=store)
    store.fail_next_save = True

    with pytest.raises(OSError, match="storage unavailable"):
        register(service)

    with pytest.raises(ResourceNotFoundError):
        service.get_device(actor(), DEVICE_ID)


def test_stop_persistence_failure_rolls_back_device_task_and_account() -> None:
    clock = MutableClock()
    store = MemoryStore()
    service = make_service(clock, store=store)
    register(service)
    bind_healthy_account(service)
    service.enqueue_task(
        actor(),
        task_id=TASK_ID,
        target_device_id=DEVICE_ID,
        task_type="social.account.health_check",
    )
    store.fail_next_save = True

    with pytest.raises(OSError, match="storage unavailable"):
        service.emergency_stop(
            actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
        )

    assert service.get_device(actor(), DEVICE_ID).status is DeviceStatus.ONLINE
    assert service.get_task(actor(), TASK_ID).status is LocalTaskStatus.QUEUED
    assert service.get_account(actor(), ACCOUNT_ID).status is AccountStatus.HEALTHY
    assert service.require_account_executable(actor(), ACCOUNT_ID).account_id == ACCOUNT_ID


def test_audit_failure_without_store_rolls_back_state() -> None:
    clock = MutableClock()
    service = make_service(clock, audit=FailingAuditSink())

    with pytest.raises(OSError, match="audit unavailable"):
        register(service)

    with pytest.raises(ResourceNotFoundError):
        service.get_device(actor(), DEVICE_ID)


def test_audit_failure_is_recovered_from_durable_outbox() -> None:
    clock = MutableClock()
    store = MemoryStore()
    service = make_service(clock, audit=FailingAuditSink(), store=store)

    register(service)

    assert service.get_device(actor(), DEVICE_ID).device_id == DEVICE_ID
    recovered_audit = InMemoryAuditSink()
    make_service(clock, audit=recovered_audit, store=store)
    assert [event.action for event in recovered_audit.events] == [
        "social.device.registered"
    ]
    assert store.state is not None
    assert store.state["audit_outbox"] == []


def test_sqlite_rejects_symlink_in_any_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        SqliteDeviceAccountStateStore(linked_parent / "nested" / "state.db")


def test_sqlite_rejects_symlink_leaf(tmp_path: Path) -> None:
    target = tmp_path / "target.db"
    target.touch()
    linked_state = tmp_path / "state.db"
    linked_state.symlink_to(target)

    with pytest.raises(ValueError, match="symbolic link"):
        SqliteDeviceAccountStateStore(linked_state)


def test_sqlite_requires_an_owner_only_private_parent(tmp_path: Path) -> None:
    public_parent = tmp_path / "public"
    public_parent.mkdir(mode=0o700)
    public_parent.chmod(0o755)

    with pytest.raises(ValueError, match="private directory"):
        SqliteDeviceAccountStateStore(public_parent / "state.db")


def test_sqlite_detects_leaf_replacement_between_validation_and_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.db"
    store = SqliteDeviceAccountStateStore(state_path)
    replacement = tmp_path / "replacement.db"
    shutil.copy2(state_path, replacement)
    real_connect = device_account_persistence.sqlite3.connect
    swapped = False

    def swapping_connect(
        path: Path, *, timeout: float
    ) -> device_account_persistence.sqlite3.Connection:
        nonlocal swapped
        if not swapped:
            swapped = True
            state_path.unlink()
            state_path.symlink_to(replacement)
        return real_connect(path, timeout=timeout)

    monkeypatch.setattr(device_account_persistence.sqlite3, "connect", swapping_connect)

    with pytest.raises(ValueError, match="changed while opening|symbolic link"):
        store.load()


def test_sqlite_multi_instance_stale_heartbeat_cannot_overwrite_emergency_stop(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.db"
    clock = MutableClock()
    first = DeviceAccountService(
        clock=clock,
        audit_sink=InMemoryAuditSink(),
        offline_after=timedelta(seconds=30),
        claim_lease=timedelta(seconds=10),
        state_store=SqliteDeviceAccountStateStore(state_path),
    )
    register(first)
    stale = DeviceAccountService(
        clock=clock,
        audit_sink=InMemoryAuditSink(),
        offline_after=timedelta(seconds=30),
        claim_lease=timedelta(seconds=10),
        state_store=SqliteDeviceAccountStateStore(state_path),
    )

    first.emergency_stop(
        actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
    )

    with pytest.raises(ConflictError, match="state.*changed|revision"):
        stale.heartbeat(
            actor(),
            device_id=DEVICE_ID,
            app_version="0.2.0",
            executor_version="1.1.0",
            heartbeat_sequence=1,
        )

    recovered = DeviceAccountService(
        clock=clock,
        audit_sink=InMemoryAuditSink(),
        offline_after=timedelta(seconds=30),
        claim_lease=timedelta(seconds=10),
        state_store=SqliteDeviceAccountStateStore(state_path),
    )
    assert recovered.get_device(actor(), DEVICE_ID).status is DeviceStatus.EMERGENCY_STOPPED


def test_sqlite_multi_instance_compare_and_swap_has_one_atomic_winner(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.db"
    first = SqliteDeviceAccountStateStore(state_path)
    second = SqliteDeviceAccountStateStore(state_path)
    revision = first.save({"writer": "initial"}, expected_revision=0)
    barrier = Barrier(2)

    def write(store: SqliteDeviceAccountStateStore, writer: str) -> str:
        barrier.wait()
        try:
            store.save({"writer": writer}, expected_revision=revision)
        except ConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda item: write(*item),
                ((first, "first"), (second, "second")),
            )
        )

    assert sorted(results) == ["conflict", "saved"]
    snapshot = SqliteDeviceAccountStateStore(state_path).load()
    assert snapshot is not None
    assert snapshot[0] == revision + 1
    assert snapshot[1]["writer"] in {"first", "second"}
