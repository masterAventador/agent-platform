from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agent_platform.capabilities.social_operations.device_account_service import (
    AccountActionResult,
    AccountHandoffReason,
    AccountHealthSignal,
    AccountStatus,
    ActorContext,
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
OTHER_TENANT_ID = UUID("00000000-0000-4000-8000-000000000102")
USER_ID = UUID("00000000-0000-4000-8000-000000000201")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000202")
DEVICE_ID = UUID("00000000-0000-4000-8000-000000000301")
OTHER_DEVICE_ID = UUID("00000000-0000-4000-8000-000000000302")
TASK_ID = UUID("00000000-0000-4000-8000-000000000401")
ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000501")


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def service(clock: MutableClock, audit: InMemoryAuditSink) -> DeviceAccountService:
    return DeviceAccountService(
        clock=clock,
        audit_sink=audit,
        offline_after=timedelta(seconds=30),
        claim_lease=timedelta(seconds=10),
    )


def actor(
    *,
    tenant_id: UUID = TENANT_ID,
    user_id: UUID = USER_ID,
    permissions: frozenset[str] = frozenset({"social.read", "social.execute", "social.manage"}),
) -> ActorContext:
    return ActorContext(tenant_id=tenant_id, user_id=user_id, permissions=permissions)


def register_device(service: DeviceAccountService) -> None:
    service.register_device(
        actor(),
        device_id=DEVICE_ID,
        display_name="Marketing Mac",
        platform=DevicePlatform.MACOS,
        app_version="0.1.0",
        executor_version="1.0.0",
        heartbeat_sequence=0,
    )


def test_device_registration_heartbeat_version_and_online_state(
    service: DeviceAccountService,
    clock: MutableClock,
) -> None:
    registered = service.register_device(
        actor(),
        device_id=DEVICE_ID,
        display_name="Marketing Mac",
        platform=DevicePlatform.MACOS,
        app_version="0.1.0",
        executor_version="1.0.0",
        heartbeat_sequence=0,
    )

    assert registered.status is DeviceStatus.ONLINE
    assert registered.owner_user_id == USER_ID
    assert registered.last_seen_at == clock.now

    clock.now += timedelta(seconds=31)
    assert service.get_device(actor(), DEVICE_ID).status is DeviceStatus.OFFLINE

    heartbeat = service.heartbeat(
        actor(),
        device_id=DEVICE_ID,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=1,
    )
    assert heartbeat.status is DeviceStatus.ONLINE
    assert heartbeat.app_version == "0.2.0"
    assert heartbeat.executor_version == "1.1.0"


def test_task_claim_is_device_scoped_and_emergency_stop_cancels_work(
    service: DeviceAccountService,
) -> None:
    register_device(service)
    service.register_device(
        actor(user_id=OTHER_USER_ID),
        device_id=OTHER_DEVICE_ID,
        display_name="Windows Runner",
        platform=DevicePlatform.WINDOWS,
        app_version="0.1.0",
        executor_version="1.0.0",
        heartbeat_sequence=0,
    )
    queued = service.enqueue_task(
        actor(),
        task_id=TASK_ID,
        target_device_id=DEVICE_ID,
        task_type="social.account.health_check",
    )
    assert queued.status is LocalTaskStatus.QUEUED

    assert service.claim_tasks(actor(user_id=OTHER_USER_ID), OTHER_DEVICE_ID, limit=10) == ()
    claimed = service.claim_tasks(actor(), DEVICE_ID, limit=1)
    assert len(claimed) == 1
    assert claimed[0].status is LocalTaskStatus.CLAIMED

    stopped = service.emergency_stop(
        actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
    )
    assert stopped.status is DeviceStatus.EMERGENCY_STOPPED
    assert service.get_task(actor(), TASK_ID).status is LocalTaskStatus.CANCELLED
    with pytest.raises(AuthorizationError, match="emergency stop"):
        service.enqueue_task(
            actor(),
            task_id=UUID("00000000-0000-4000-8000-000000000402"),
            target_device_id=DEVICE_ID,
            task_type="social.account.health_check",
        )


def test_device_and_account_access_enforce_permission_tenant_and_owner(
    service: DeviceAccountService,
) -> None:
    register_device(service)

    with pytest.raises(AuthorizationError, match="social.execute"):
        service.heartbeat(
            actor(permissions=frozenset({"social.read"})),
            device_id=DEVICE_ID,
            app_version="0.1.0",
            executor_version="1.0.0",
            heartbeat_sequence=1,
        )
    with pytest.raises(ResourceNotFoundError):
        service.get_device(actor(tenant_id=OTHER_TENANT_ID), DEVICE_ID)
    with pytest.raises(ResourceNotFoundError):
        service.claim_tasks(actor(user_id=OTHER_USER_ID), DEVICE_ID, limit=1)


@pytest.mark.parametrize(
    "signal",
    [AccountHealthSignal.CAPTCHA_REQUIRED, AccountHealthSignal.RISK_CONTROL],
)
def test_account_risk_requires_human_handoff_and_opens_circuit(
    service: DeviceAccountService,
    audit: InMemoryAuditSink,
    signal: AccountHealthSignal,
) -> None:
    register_device(service)
    bound = service.bind_account(
        actor(),
        account_id=ACCOUNT_ID,
        platform=SocialPlatform.DOUYIN,
        display_name="Demo Publisher",
        device_id=DEVICE_ID,
    )
    assert bound.tenant_id == TENANT_ID
    assert bound.owner_user_id == USER_ID
    assert bound.device_id == DEVICE_ID
    assert bound.status is AccountStatus.AWAITING_SCAN
    assert bound.circuit_open is True

    authenticated = service.report_account_health(
        actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED
    )
    assert authenticated.status is AccountStatus.HEALTHY

    handed_off = service.report_account_health(actor(), ACCOUNT_ID, signal=signal)
    assert handed_off.status is AccountStatus.HUMAN_HANDOFF
    assert handed_off.circuit_open is True
    assert handed_off.handoff_reason == signal.value

    with pytest.raises(AuthorizationError, match="circuit"):
        service.require_account_executable(actor(), ACCOUNT_ID)
    with pytest.raises(AuthorizationError, match="human handoff"):
        service.report_account_health(
            actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED
        )

    assert [event.action for event in audit.events] == [
        "social.device.registered",
        "social.account.bound",
        "social.account.health_changed",
        "social.account.handoff_requested",
    ]


def test_logout_revokes_account_session_without_storing_cookie_material(
    service: DeviceAccountService,
    audit: InMemoryAuditSink,
) -> None:
    register_device(service)
    service.bind_account(
        actor(),
        account_id=ACCOUNT_ID,
        platform=SocialPlatform.DOUYIN,
        display_name="Demo Publisher",
        device_id=DEVICE_ID,
    )
    service.report_account_health(actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED)

    logged_out = service.logout_account(actor(), ACCOUNT_ID)

    assert logged_out.status is AccountStatus.LOGGED_OUT
    assert logged_out.circuit_open is True
    assert logged_out.session_revision == 1
    serialized = repr(logged_out) + repr(audit.events)
    assert "cookie" not in serialized.casefold()
    assert "token" not in serialized.casefold()


def test_heartbeat_replay_is_idempotent_and_rejects_stale_conflicts(
    service: DeviceAccountService,
    clock: MutableClock,
) -> None:
    register_device(service)
    clock.now += timedelta(seconds=1)
    first = service.heartbeat(
        actor(),
        device_id=DEVICE_ID,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=1,
    )
    replay = service.heartbeat(
        actor(),
        device_id=DEVICE_ID,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=1,
    )
    assert replay == first

    with pytest.raises(ConflictError, match="heartbeat sequence"):
        service.heartbeat(
            actor(),
            device_id=DEVICE_ID,
            app_version="tampered",
            executor_version="1.1.0",
            heartbeat_sequence=1,
        )


def test_task_claim_is_atomic_and_expired_claim_is_recovered_once(
    service: DeviceAccountService,
    clock: MutableClock,
) -> None:
    register_device(service)
    service.enqueue_task(
        actor(),
        task_id=TASK_ID,
        target_device_id=DEVICE_ID,
        task_type="social.account.health_check",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(lambda _: service.claim_tasks(actor(), DEVICE_ID, limit=1), range(2))
        )
    claimed = tuple(task for batch in results for task in batch)
    assert [task.task_id for task in claimed] == [TASK_ID]
    assert claimed[0].claim_attempt == 1

    clock.now += timedelta(seconds=11)
    recovered = service.claim_tasks(actor(), DEVICE_ID, limit=1)
    assert len(recovered) == 1
    assert recovered[0].task_id == TASK_ID
    assert recovered[0].claim_attempt == 2


def test_operator_must_explicitly_resume_account_after_handoff(
    service: DeviceAccountService,
) -> None:
    register_device(service)
    service.bind_account(
        actor(),
        account_id=ACCOUNT_ID,
        platform=SocialPlatform.DOUYIN,
        display_name="Demo Publisher",
        device_id=DEVICE_ID,
    )
    service.report_account_health(
        actor(), ACCOUNT_ID, signal=AccountHealthSignal.CAPTCHA_REQUIRED
    )

    resumed = service.resume_account_after_handoff(actor(), ACCOUNT_ID)
    assert resumed.status is AccountStatus.AWAITING_SCAN
    assert resumed.circuit_open is True
    assert resumed.session_revision == 1
    authenticated = service.report_account_health(
        actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED
    )
    assert authenticated.status is AccountStatus.HEALTHY


def test_platform_account_requires_supported_platform_enum() -> None:
    with pytest.raises(ValueError):
        SocialPlatform("unsupported")


def test_reregister_cannot_clear_device_emergency_stop(service: DeviceAccountService) -> None:
    register_device(service)
    service.emergency_stop(
        actor(), DEVICE_ID, reason=EmergencyStopReason.OPERATOR_REQUESTED
    )

    replay = service.register_device(
        actor(),
        device_id=DEVICE_ID,
        display_name="Marketing Mac",
        platform=DevicePlatform.MACOS,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=0,
    )

    assert replay.status is DeviceStatus.EMERGENCY_STOPPED


def test_account_governance_cold_start_daily_limit_is_atomic(
    service: DeviceAccountService,
) -> None:
    register_device(service)
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
    service.configure_account_governance_policy(
        actor(),
        platform=SocialPlatform.DOUYIN,
        action_type="publish_video",
        min_interval=timedelta(0),
        daily_limit=10,
        cold_start_daily_limit=2,
        cold_start_days=7,
        consecutive_failure_threshold=3,
    )

    def authorize_once(index: int) -> bool:
        try:
            service.authorize_account_action(
                actor(),
                ACCOUNT_ID,
                action_type="publish_video",
                idempotency_key=f"publish-{index}",
            )
        except AuthorizationError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = tuple(executor.map(authorize_once, range(5)))

    assert results.count(True) == 2
    assert results.count(False) == 3
    snapshot = service.get_account_governance(actor(), ACCOUNT_ID)
    assert snapshot.health_score == 100
    assert snapshot.policy_limits["publish_video"].daily_limit == 10
    assert snapshot.policy_limits["publish_video"].effective_daily_limit == 2
    assert snapshot.policy_limits["publish_video"].remaining_daily == 0
    assert snapshot.recommendations == ("冷启动账号今日已达到 publish_video 上限，请明日再执行。",)


def test_account_governance_rate_window_blocks_immediate_retry(
    service: DeviceAccountService,
    clock: MutableClock,
) -> None:
    register_device(service)
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
    service.configure_account_governance_policy(
        actor(),
        platform=SocialPlatform.DOUYIN,
        action_type="comment",
        min_interval=timedelta(seconds=60),
        daily_limit=5,
        cold_start_daily_limit=5,
        cold_start_days=7,
        consecutive_failure_threshold=3,
    )

    first = service.authorize_account_action(
        actor(),
        ACCOUNT_ID,
        action_type="comment",
        idempotency_key="comment-1",
    )
    assert first.allowed is True
    assert first.remaining_daily == 4

    with pytest.raises(AuthorizationError, match="frequency"):
        service.authorize_account_action(
            actor(),
            ACCOUNT_ID,
            action_type="comment",
            idempotency_key="comment-2",
        )

    clock.now += timedelta(seconds=60)
    service.heartbeat(
        actor(),
        device_id=DEVICE_ID,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=1,
    )
    second = service.authorize_account_action(
        actor(),
        ACCOUNT_ID,
        action_type="comment",
        idempotency_key="comment-2",
    )
    assert second.allowed is True
    assert second.remaining_daily == 3


def test_consecutive_failures_open_circuit_and_surface_recommendations(
    service: DeviceAccountService,
    audit: InMemoryAuditSink,
) -> None:
    register_device(service)
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
    service.configure_account_governance_policy(
        actor(),
        platform=SocialPlatform.DOUYIN,
        action_type="private_message",
        min_interval=timedelta(0),
        daily_limit=10,
        cold_start_daily_limit=10,
        cold_start_days=7,
        consecutive_failure_threshold=3,
    )

    for index in range(3):
        service.authorize_account_action(
            actor(),
            ACCOUNT_ID,
            action_type="private_message",
            idempotency_key=f"dm-{index}",
        )
        service.record_account_action_result(
            actor(),
            ACCOUNT_ID,
            action_type="private_message",
            result=AccountActionResult.FAILED,
            idempotency_key=f"dm-{index}",
            failure_reason="platform_rejected",
        )

    account = service.get_account(actor(), ACCOUNT_ID)
    assert account.status is AccountStatus.HUMAN_HANDOFF
    assert account.circuit_open is True
    assert account.handoff_reason is AccountHandoffReason.CONSECUTIVE_FAILURES
    with pytest.raises(AuthorizationError, match="circuit"):
        service.authorize_account_action(
            actor(),
            ACCOUNT_ID,
            action_type="private_message",
            idempotency_key="dm-blocked",
        )

    snapshot = service.get_account_governance(actor(), ACCOUNT_ID)
    assert snapshot.health_score == 40
    assert snapshot.failure_trend["private_message"] == 3
    assert snapshot.recent_tasks[0].result is AccountActionResult.FAILED
    assert "连续失败" in snapshot.recommendations[0]
    assert "failure_reason" not in repr(audit.events).casefold()
    assert "platform_rejected" not in repr(audit.events)
    assert "social.account.circuit_opened" in [
        event.action for event in audit.events
    ]


def test_action_result_requires_authorization_and_is_idempotent(
    service: DeviceAccountService,
) -> None:
    register_device(service)
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
    service.configure_account_governance_policy(
        actor(),
        platform=SocialPlatform.DOUYIN,
        action_type="private_message",
        min_interval=timedelta(0),
        daily_limit=10,
        cold_start_daily_limit=10,
        cold_start_days=7,
        consecutive_failure_threshold=3,
    )

    with pytest.raises(AuthorizationError, match="not authorized"):
        service.record_account_action_result(
            actor(),
            ACCOUNT_ID,
            action_type="private_message",
            result=AccountActionResult.FAILED,
            idempotency_key="missing-authorization",
        )

    service.authorize_account_action(
        actor(),
        ACCOUNT_ID,
        action_type="private_message",
        idempotency_key="dm-1",
    )
    failed = service.record_account_action_result(
        actor(),
        ACCOUNT_ID,
        action_type="private_message",
        result=AccountActionResult.FAILED,
        idempotency_key="dm-1",
    )
    replayed = service.record_account_action_result(
        actor(),
        ACCOUNT_ID,
        action_type="private_message",
        result=AccountActionResult.FAILED,
        idempotency_key="dm-1",
    )

    assert failed.failure_trend["private_message"] == 1
    assert replayed.failure_trend["private_message"] == 1
    assert len([
        task for task in replayed.recent_tasks if task.idempotency_key == "dm-1"
    ]) == 1

    with pytest.raises(ConflictError, match="already recorded"):
        service.record_account_action_result(
            actor(),
            ACCOUNT_ID,
            action_type="private_message",
            result=AccountActionResult.SUCCEEDED,
            idempotency_key="dm-1",
        )


def test_login_expired_and_abnormal_behavior_enter_handoff(
    service: DeviceAccountService,
) -> None:
    register_device(service)
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

    expired = service.report_account_health(
        actor(), ACCOUNT_ID, signal=AccountHealthSignal.LOGIN_EXPIRED
    )
    assert expired.status is AccountStatus.HUMAN_HANDOFF
    assert expired.handoff_reason is AccountHandoffReason.LOGIN_EXPIRED

    service.resume_account(actor(), ACCOUNT_ID)
    service.report_account_health(
        actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED
    )
    service.configure_account_governance_policy(
        actor(),
        platform=SocialPlatform.DOUYIN,
        action_type="follow",
        min_interval=timedelta(0),
        daily_limit=10,
        cold_start_daily_limit=10,
        cold_start_days=7,
        consecutive_failure_threshold=99,
    )
    service.authorize_account_action(
        actor(),
        ACCOUNT_ID,
        action_type="follow",
        idempotency_key="follow-1",
    )
    snapshot = service.record_account_action_result(
        actor(),
        ACCOUNT_ID,
        action_type="follow",
        result=AccountActionResult.ABNORMAL_BEHAVIOR,
        idempotency_key="follow-1",
        failure_reason="sensitive-platform-message",
    )

    account = service.get_account(actor(), ACCOUNT_ID)
    assert account.status is AccountStatus.HUMAN_HANDOFF
    assert account.handoff_reason is AccountHandoffReason.ABNORMAL_BEHAVIOR
    assert "异常行为" in snapshot.recommendations[0]


def test_pause_resume_offline_and_remote_stop_govern_action_execution(
    service: DeviceAccountService,
    clock: MutableClock,
) -> None:
    register_device(service)
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
    service.configure_account_governance_policy(
        actor(),
        platform=SocialPlatform.DOUYIN,
        action_type="publish_video",
        min_interval=timedelta(0),
        daily_limit=5,
        cold_start_daily_limit=5,
        cold_start_days=7,
        consecutive_failure_threshold=3,
    )

    paused = service.pause_account(actor(), ACCOUNT_ID, reason="operator_review")
    assert paused.status is AccountStatus.PAUSED
    with pytest.raises(
        AuthorizationError, match="paused account requires explicit operator resume"
    ):
        service.report_account_health(
            actor(), ACCOUNT_ID, signal=AccountHealthSignal.AUTHENTICATED
        )
    assert service.get_account(actor(), ACCOUNT_ID).status is AccountStatus.PAUSED
    with pytest.raises(AuthorizationError, match="paused"):
        service.authorize_account_action(
            actor(),
            ACCOUNT_ID,
            action_type="publish_video",
            idempotency_key="paused",
        )

    resumed = service.resume_account(actor(), ACCOUNT_ID)
    assert resumed.status is AccountStatus.HEALTHY
    service.authorize_account_action(
        actor(),
        ACCOUNT_ID,
        action_type="publish_video",
        idempotency_key="after-resume",
    )

    clock.now += timedelta(seconds=31)
    with pytest.raises(AuthorizationError, match="device is not online"):
        service.authorize_account_action(
            actor(),
            ACCOUNT_ID,
            action_type="publish_video",
            idempotency_key="offline",
        )
    assert service.get_account_governance(
        actor(), ACCOUNT_ID
    ).policy_limits["publish_video"].remaining_daily == 4

    service.heartbeat(
        actor(),
        device_id=DEVICE_ID,
        app_version="0.2.0",
        executor_version="1.1.0",
        heartbeat_sequence=1,
    )
    stopped = service.remote_stop_account(actor(), ACCOUNT_ID, reason="remote_stop")
    assert stopped.status is AccountStatus.HUMAN_HANDOFF
    assert stopped.circuit_open is True
    assert stopped.handoff_reason is AccountHandoffReason.REMOTE_STOP
