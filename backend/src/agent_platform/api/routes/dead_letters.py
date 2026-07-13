from collections.abc import Mapping
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.queue.dead_letters import (
    DeadLetterNotReplayable,
    DeadLetterNotSettled,
    ReplayedRun,
    RunDeadLetter,
    RunDeadLetterService,
)
from agent_platform.platform.tenants.permissions import TenantPermission

router = APIRouter(prefix="/api/v1", tags=["run-dead-letters"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
_KNOWN_QUEUE_FIELD_NAMES = frozenset(
    {"command_id", "run_id", "tenant_id", "action", "payload"}
)
_HEX_DIGITS = frozenset("0123456789abcdef")
_MAX_UNKNOWN_FIELD_FINGERPRINTS = 32


class RawFieldFingerprintResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    length: Annotated[int, Field(ge=0)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RawFieldsSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    known_field_keys: list[str] = Field(default_factory=list)
    unknown_fields: list[RawFieldFingerprintResponse] = Field(default_factory=list)
    field_count: Annotated[int, Field(ge=0)] = 0
    total_bytes: Annotated[int, Field(ge=0)] = 0
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


def _safe_non_negative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _safe_sha256(value: object) -> str | None:
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    ):
        return value
    return None


def _safe_raw_fields_summary(
    summary: Mapping[str, object],
) -> RawFieldsSummaryResponse:
    raw_known_field_keys = summary.get("known_field_keys")
    known_field_keys = (
        [
            value
            for value in raw_known_field_keys
            if isinstance(value, str) and value in _KNOWN_QUEUE_FIELD_NAMES
        ]
        if isinstance(raw_known_field_keys, list)
        else []
    )

    unknown_fields: list[RawFieldFingerprintResponse] = []
    raw_unknown_fields = summary.get("unknown_fields")
    if isinstance(raw_unknown_fields, list):
        for value in raw_unknown_fields[:_MAX_UNKNOWN_FIELD_FINGERPRINTS]:
            if not isinstance(value, dict):
                continue
            sha256 = _safe_sha256(value.get("sha256"))
            if sha256 is None:
                continue
            unknown_fields.append(
                RawFieldFingerprintResponse(
                    length=_safe_non_negative_int(value.get("length")),
                    sha256=sha256,
                )
            )

    return RawFieldsSummaryResponse(
        known_field_keys=known_field_keys,
        unknown_fields=unknown_fields,
        field_count=_safe_non_negative_int(summary.get("field_count")),
        total_bytes=_safe_non_negative_int(summary.get("total_bytes")),
        sha256=_safe_sha256(summary.get("sha256")),
    )


class RunDeadLetterResponse(BaseModel):
    id: UUID
    original_command_id: UUID | None
    original_run_id: UUID | None
    action: str | None
    attempts: int
    error_type: str
    is_malformed: bool
    raw_fields_summary: RawFieldsSummaryResponse
    failed_at: datetime
    settled_run_id: UUID | None
    replayed_run_id: UUID | None
    replayed_command_id: UUID | None
    replayed_at: datetime | None
    mirrored_at: datetime | None

    @classmethod
    def from_entity(cls, dead_letter: RunDeadLetter) -> "RunDeadLetterResponse":
        return cls(
            id=dead_letter.id,
            original_command_id=dead_letter.original_command_id,
            original_run_id=dead_letter.original_run_id,
            action=dead_letter.action,
            attempts=dead_letter.attempts,
            error_type=dead_letter.error_type,
            is_malformed=dead_letter.is_malformed,
            raw_fields_summary=_safe_raw_fields_summary(dead_letter.raw_fields_summary),
            failed_at=dead_letter.failed_at,
            settled_run_id=dead_letter.settled_run_id,
            replayed_run_id=dead_letter.replayed_run_id,
            replayed_command_id=dead_letter.replayed_command_id,
            replayed_at=dead_letter.replayed_at,
            mirrored_at=dead_letter.mirrored_at,
        )


class ReplayDeadLetterResponse(BaseModel):
    run_id: UUID
    command_id: UUID

    @classmethod
    def from_entity(cls, replayed: ReplayedRun) -> "ReplayDeadLetterResponse":
        return cls(run_id=replayed.run_id, command_id=replayed.command_id)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _conflict(*, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


@router.get(
    "/run-dead-letters",
    response_model=list[RunDeadLetterResponse],
    responses={status.HTTP_403_FORBIDDEN: {"description": "权限不足"}},
)
async def list_run_dead_letters(
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[RunDeadLetterResponse]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.OPERATIONS_MANAGE,
        )

    dead_letters = await RunDeadLetterService(
        session_factory=request.app.state.session_factory
    ).list(tenant_id=access.tenant.id, limit=limit)
    return [RunDeadLetterResponse.from_entity(dead_letter) for dead_letter in dead_letters]


@router.post(
    "/run-dead-letters/{dead_letter_id}/replay",
    response_model=ReplayDeadLetterResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "权限不足"},
        status.HTTP_404_NOT_FOUND: {"description": "死信不存在"},
        status.HTTP_409_CONFLICT: {"description": "死信不能重放"},
    },
)
async def replay_run_dead_letter(
    dead_letter_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ReplayDeadLetterResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.OPERATIONS_MANAGE,
        )

    service = RunDeadLetterService(session_factory=request.app.state.session_factory)
    try:
        replayed = await service.replay(
            tenant_id=access.tenant.id,
            dead_letter_id=dead_letter_id,
            operator_user_id=user.id,
        )
    except LookupError as error:
        raise _not_found() from error
    except DeadLetterNotReplayable as error:
        raise _conflict(
            code="dead_letter_not_replayable",
            message="该死信不能安全重放",
        ) from error
    except DeadLetterNotSettled as error:
        raise _conflict(
            code="dead_letter_not_settled",
            message="该死信尚未完成结算",
        ) from error
    return ReplayDeadLetterResponse.from_entity(replayed)
