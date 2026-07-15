from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.capabilities.social_operations.device_account_service import (
    AccountHealthSignal,
    ActorContext,
    AuthorizationError,
    ConflictError,
    DeviceAccountService,
    DevicePlatform,
    EmergencyStopReason,
    ResourceNotFoundError,
    SocialPlatform,
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterDeviceRequest(_Request):
    device_id: UUID
    display_name: str = Field(min_length=1, max_length=128)
    platform: DevicePlatform
    app_version: str = Field(min_length=1, max_length=128)
    executor_version: str = Field(min_length=1, max_length=128)


class HeartbeatRequest(_Request):
    app_version: str = Field(min_length=1, max_length=128)
    executor_version: str = Field(min_length=1, max_length=128)
    heartbeat_sequence: int = Field(ge=1)


class EmergencyStopRequest(_Request):
    reason: str = Field(
        json_schema_extra={"enum": [reason.value for reason in EmergencyStopReason]}
    )


class EnqueueTaskRequest(_Request):
    task_id: UUID
    target_device_id: UUID
    task_type: str = Field(min_length=1, max_length=128, pattern=r"^social\.")


class ClaimTasksRequest(_Request):
    limit: int = Field(default=1, ge=1, le=100)


class BindAccountRequest(_Request):
    account_id: UUID
    platform: SocialPlatform
    display_name: str = Field(min_length=1, max_length=128)
    device_id: UUID


class AccountHealthRequest(_Request):
    signal: AccountHealthSignal


def create_device_account_router(
    service: DeviceAccountService,
    *,
    actor_provider: Callable[[], ActorContext],
) -> APIRouter:
    """Build the isolated B02 router; C17 owns production host registration."""

    router = APIRouter(prefix="/api/v1/social-operations")

    @router.post("/devices/register", status_code=status.HTTP_201_CREATED)
    def register_device(request: RegisterDeviceRequest) -> Any:
        return _call(
            lambda: service.register_device(
                actor_provider(),
                device_id=request.device_id,
                display_name=request.display_name,
                platform=request.platform,
                app_version=request.app_version,
                executor_version=request.executor_version,
                heartbeat_sequence=0,
            )
        )

    @router.post("/devices/{device_id}/heartbeat")
    def heartbeat(device_id: UUID, request: HeartbeatRequest) -> Any:
        return _call(
            lambda: service.heartbeat(
                actor_provider(),
                device_id=device_id,
                app_version=request.app_version,
                executor_version=request.executor_version,
                heartbeat_sequence=request.heartbeat_sequence,
            )
        )

    @router.get("/devices/{device_id}")
    def get_device(device_id: UUID) -> Any:
        return _call(lambda: service.get_device(actor_provider(), device_id))

    @router.get("/devices")
    def list_devices() -> Any:
        return _call(lambda: service.list_devices(actor_provider()))

    @router.post("/devices/{device_id}/emergency-stop")
    def emergency_stop(device_id: UUID, request: EmergencyStopRequest) -> Any:
        try:
            reason = EmergencyStopReason(request.reason)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="invalid emergency stop reason",
            ) from None
        return _call(
            lambda: service.emergency_stop(
                actor_provider(), device_id, reason=reason
            )
        )

    @router.post("/local-tasks", status_code=status.HTTP_201_CREATED)
    def enqueue_task(request: EnqueueTaskRequest) -> Any:
        return _call(
            lambda: service.enqueue_task(
                actor_provider(),
                task_id=request.task_id,
                target_device_id=request.target_device_id,
                task_type=request.task_type,
            )
        )

    @router.post("/devices/{device_id}/claims")
    def claim_tasks(device_id: UUID, request: ClaimTasksRequest) -> Any:
        return _call(
            lambda: service.claim_tasks(
                actor_provider(), device_id, limit=request.limit
            )
        )

    @router.post("/accounts", status_code=status.HTTP_201_CREATED)
    def bind_account(request: BindAccountRequest) -> Any:
        return _call(
            lambda: service.bind_account(
                actor_provider(),
                account_id=request.account_id,
                platform=request.platform,
                display_name=request.display_name,
                device_id=request.device_id,
            )
        )

    @router.post("/accounts/{account_id}/health")
    def report_account_health(account_id: UUID, request: AccountHealthRequest) -> Any:
        return _call(
            lambda: service.report_account_health(
                actor_provider(), account_id, signal=request.signal
            )
        )

    @router.get("/accounts/{account_id}")
    def get_account(account_id: UUID) -> Any:
        return _call(lambda: service.get_account(actor_provider(), account_id))

    @router.get("/accounts")
    def list_accounts() -> Any:
        return _call(lambda: service.list_accounts(actor_provider()))

    @router.post("/accounts/{account_id}/resume")
    def resume_account(account_id: UUID) -> Any:
        return _call(
            lambda: service.resume_account_after_handoff(actor_provider(), account_id)
        )

    @router.post("/accounts/{account_id}/logout")
    def logout_account(account_id: UUID) -> Any:
        return _call(lambda: service.logout_account(actor_provider(), account_id))

    return router


def _call(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except AuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from None
    except ResourceNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from None
    except ConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from None
