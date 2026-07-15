from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.capabilities.social_operations.device_account_api import (
    create_device_account_router,
)
from agent_platform.capabilities.social_operations.device_account_persistence import (
    SqliteDeviceAccountStateStore,
)
from agent_platform.capabilities.social_operations.device_account_service import (
    ActorContext,
    DeviceAccountService,
    InMemoryAuditSink,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000101")
USER_ID = UUID("00000000-0000-4000-8000-000000000201")
DEVICE_ID = UUID("00000000-0000-4000-8000-000000000301")
ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000501")


def full_actor() -> ActorContext:
    return ActorContext(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        permissions=frozenset({"social.read", "social.execute", "social.manage"}),
    )


def make_service(state_path: Path) -> DeviceAccountService:
    return DeviceAccountService(
        clock=lambda: datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
        audit_sink=InMemoryAuditSink(),
        offline_after=timedelta(seconds=30),
        claim_lease=timedelta(seconds=10),
        state_store=SqliteDeviceAccountStateStore(state_path),
    )


def make_client(service: DeviceAccountService, actor: ActorContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_device_account_router(service, actor_provider=lambda: actor or full_actor())
    )
    return TestClient(app)


def test_sqlite_state_survives_service_restart_without_plaintext_session_material(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "social-operations.db"
    service = make_service(state_path)
    client = make_client(service)

    assert client.post(
        "/api/v1/social-operations/devices/register",
        json={
            "device_id": str(DEVICE_ID),
            "display_name": "Marketing Mac",
            "platform": "macos",
            "app_version": "0.1.0",
            "executor_version": "1.0.0",
        },
    ).status_code == 201
    assert client.post(
        "/api/v1/social-operations/accounts",
        json={
            "account_id": str(ACCOUNT_ID),
            "platform": "douyin",
            "display_name": "Demo Publisher",
            "device_id": str(DEVICE_ID),
        },
    ).status_code == 201

    restarted = make_service(state_path)
    restarted_client = make_client(restarted)
    device = restarted_client.get(
        f"/api/v1/social-operations/devices/{DEVICE_ID}"
    ).json()
    assert device["device_id"] == str(DEVICE_ID)
    assert device["platform"] == "macos"
    account = restarted_client.get(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}"
    ).json()
    assert account["account_id"] == str(ACCOUNT_ID)
    assert account["device_id"] == str(DEVICE_ID)
    assert [item["device_id"] for item in restarted_client.get(
        "/api/v1/social-operations/devices"
    ).json()] == [str(DEVICE_ID)]
    assert [item["account_id"] for item in restarted_client.get(
        "/api/v1/social-operations/accounts"
    ).json()] == [str(ACCOUNT_ID)]
    persisted_bytes = state_path.read_bytes().lower()
    assert b"cookie" not in persisted_bytes
    assert b"token" not in persisted_bytes


def test_api_exposes_heartbeat_claim_stop_and_handoff_without_bypass(tmp_path: Path) -> None:
    client = make_client(make_service(tmp_path / "social-operations.db"))
    client.post(
        "/api/v1/social-operations/devices/register",
        json={
            "device_id": str(DEVICE_ID),
            "display_name": "Marketing Mac",
            "platform": "macos",
            "app_version": "0.1.0",
            "executor_version": "1.0.0",
        },
    )
    heartbeat = client.post(
        f"/api/v1/social-operations/devices/{DEVICE_ID}/heartbeat",
        json={
            "app_version": "0.2.0",
            "executor_version": "1.1.0",
            "heartbeat_sequence": 1,
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["heartbeat_sequence"] == 1

    task_id = "00000000-0000-4000-8000-000000000401"
    assert client.post(
        "/api/v1/social-operations/local-tasks",
        json={
            "task_id": task_id,
            "target_device_id": str(DEVICE_ID),
            "task_type": "social.account.health_check",
        },
    ).status_code == 201
    claimed = client.post(
        f"/api/v1/social-operations/devices/{DEVICE_ID}/claims", json={"limit": 1}
    )
    assert claimed.status_code == 200
    assert claimed.json()[0]["task_id"] == task_id

    assert client.post(
        "/api/v1/social-operations/accounts",
        json={
            "account_id": str(ACCOUNT_ID),
            "platform": "douyin",
            "display_name": "Demo Publisher",
            "device_id": str(DEVICE_ID),
        },
    ).status_code == 201
    handoff = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/health",
        json={"signal": "captcha_required"},
    )
    assert handoff.status_code == 200
    assert handoff.json()["status"] == "human_handoff"
    assert handoff.json()["circuit_open"] is True
    automatic_retry = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/health",
        json={"signal": "authenticated"},
    )
    assert automatic_retry.status_code == 403
    assert automatic_retry.json() == {"detail": "human handoff requires explicit operator resume"}

    stopped = client.post(
        f"/api/v1/social-operations/devices/{DEVICE_ID}/emergency-stop",
        json={"reason": "operator_requested"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "emergency_stopped"


def test_api_maps_permission_failure_without_resource_or_exception_leak(tmp_path: Path) -> None:
    service = make_service(tmp_path / "social-operations.db")
    owner_client = make_client(service)
    owner_client.post(
        "/api/v1/social-operations/devices/register",
        json={
            "device_id": str(DEVICE_ID),
            "display_name": "Marketing Mac",
            "platform": "macos",
            "app_version": "0.1.0",
            "executor_version": "1.0.0",
        },
    )
    read_only_client = make_client(
        service,
        ActorContext(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            permissions=frozenset({"social.read"}),
        ),
    )

    response = read_only_client.post(
        f"/api/v1/social-operations/devices/{DEVICE_ID}/heartbeat",
        json={
            "app_version": "0.2.0",
            "executor_version": "1.1.0",
            "heartbeat_sequence": 1,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "missing permission: social.execute"}


def test_api_rejects_free_form_emergency_stop_reason(tmp_path: Path) -> None:
    client = make_client(make_service(tmp_path / "social-operations.db"))
    response = client.post(
        f"/api/v1/social-operations/devices/{DEVICE_ID}/emergency-stop",
        json={"reason": "token=must-not-enter-audit"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid emergency stop reason"}
    assert "token" not in response.text.casefold()


def test_api_authorizes_governed_actions_without_client_policy_override(
    tmp_path: Path,
) -> None:
    client = make_client(make_service(tmp_path / "social-operations.db"))
    client.post(
        "/api/v1/social-operations/devices/register",
        json={
            "device_id": str(DEVICE_ID),
            "display_name": "Marketing Mac",
            "platform": "macos",
            "app_version": "0.1.0",
            "executor_version": "1.0.0",
        },
    )
    client.post(
        "/api/v1/social-operations/accounts",
        json={
            "account_id": str(ACCOUNT_ID),
            "platform": "douyin",
            "display_name": "Demo Publisher",
            "device_id": str(DEVICE_ID),
        },
    )
    client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/health",
        json={"signal": "authenticated"},
    )
    assert client.post(
        "/api/v1/social-operations/governance/policies",
        json={
            "platform": "douyin",
            "action_type": "publish_video",
            "min_interval_seconds": 0,
            "daily_limit": 3,
            "cold_start_daily_limit": 1,
            "cold_start_days": 7,
            "consecutive_failure_threshold": 2,
        },
    ).status_code == 201

    overridden = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/authorize",
        json={
            "action_type": "publish_video",
            "idempotency_key": "publish-1",
            "daily_limit": 999,
        },
    )
    assert overridden.status_code == 422

    allowed = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/authorize",
        json={
            "action_type": "publish_video",
            "idempotency_key": "publish-1",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True
    assert allowed.json()["remaining_daily"] == 0

    blocked = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/authorize",
        json={
            "action_type": "publish_video",
            "idempotency_key": "publish-2",
        },
    )
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": "daily limit exceeded"}


def test_api_pause_resume_remote_stop_and_governance_snapshot(tmp_path: Path) -> None:
    client = make_client(make_service(tmp_path / "social-operations.db"))
    client.post(
        "/api/v1/social-operations/devices/register",
        json={
            "device_id": str(DEVICE_ID),
            "display_name": "Marketing Mac",
            "platform": "macos",
            "app_version": "0.1.0",
            "executor_version": "1.0.0",
        },
    )
    client.post(
        "/api/v1/social-operations/accounts",
        json={
            "account_id": str(ACCOUNT_ID),
            "platform": "douyin",
            "display_name": "Demo Publisher",
            "device_id": str(DEVICE_ID),
        },
    )
    client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/health",
        json={"signal": "authenticated"},
    )
    client.post(
        "/api/v1/social-operations/governance/policies",
        json={
            "platform": "douyin",
            "action_type": "private_message",
            "min_interval_seconds": 0,
            "daily_limit": 10,
            "cold_start_daily_limit": 10,
            "cold_start_days": 7,
            "consecutive_failure_threshold": 2,
        },
    )

    paused = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/pause",
        json={"reason": "operator_review"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    blocked = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/authorize",
        json={"action_type": "private_message", "idempotency_key": "blocked"},
    )
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": "account is paused"}

    resumed = client.post(f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "healthy"

    missing_idempotency = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/result",
        json={
            "action_type": "private_message",
            "result": "failed",
        },
    )
    assert missing_idempotency.status_code == 422
    not_authorized = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/result",
        json={
            "action_type": "private_message",
            "result": "failed",
            "idempotency_key": "missing-authorization",
        },
    )
    assert not_authorized.status_code == 403
    assert not_authorized.json() == {"detail": "action result was not authorized"}

    for index in range(2):
        client.post(
            f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/authorize",
            json={
                "action_type": "private_message",
                "idempotency_key": f"dm-{index}",
            },
        )
        client.post(
            f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/result",
            json={
                "action_type": "private_message",
                "result": "failed",
                "idempotency_key": f"dm-{index}",
                "failure_reason": "token=redacted-by-service",
            },
        )
    duplicate = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/result",
        json={
            "action_type": "private_message",
            "result": "failed",
            "idempotency_key": "dm-0",
        },
    )
    assert duplicate.status_code == 200
    conflicting = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/actions/result",
        json={
            "action_type": "private_message",
            "result": "succeeded",
            "idempotency_key": "dm-0",
        },
    )
    assert conflicting.status_code == 409
    assert conflicting.json() == {"detail": "action result already recorded"}

    snapshot = client.get(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/governance"
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["health_score"] == 60
    assert len(snapshot.json()["recent_tasks"]) == 2
    assert snapshot.json()["failure_trend"] == {"private_message": 2}
    assert "连续失败" in snapshot.json()["recommendations"][0]
    assert "token" not in snapshot.text.casefold()

    stopped = client.post(
        f"/api/v1/social-operations/accounts/{ACCOUNT_ID}/remote-stop",
        json={"reason": "remote_stop"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "human_handoff"
    assert stopped.json()["handoff_reason"] == "remote_stop"
