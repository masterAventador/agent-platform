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
