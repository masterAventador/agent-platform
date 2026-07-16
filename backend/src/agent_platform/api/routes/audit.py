from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import (
    AuditEvent,
    SqlAlchemyAuditEventRepository,
)
from agent_platform.platform.tenants.permissions import TenantPermission

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    actor_user_id: UUID | None
    sequence: int
    action: str
    resource_type: str
    resource_id: UUID | None
    outcome: str
    occurred_at: str
    correlation_id: str | None
    previous_hash: str | None
    event_hash: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_entity(cls, event: AuditEvent) -> "AuditEventResponse":
        return cls(
            id=event.id,
            tenant_id=event.tenant_id,
            actor_user_id=event.actor_user_id,
            sequence=event.sequence,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            occurred_at=event.occurred_at.isoformat(),
            correlation_id=event.correlation_id,
            previous_hash=event.previous_hash,
            event_hash=event.event_hash,
            metadata=event.metadata,
        )


class AuditIntegrityResponse(BaseModel):
    valid: bool
    checked_events: int
    first_invalid_sequence: int | None = None


async def _list_events(
    *,
    request: Request,
    tenant_id: UUID | None,
    limit: int,
    action: str | None = None,
    resource_type: str | None = None,
    actor_user_id: UUID | None = None,
) -> list[AuditEventResponse]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.OPERATIONS_MANAGE,
        )
        events = await SqlAlchemyAuditEventRepository(database_session).list(
            tenant_id=access.tenant.id,
            limit=limit,
            action=action,
            resource_type=resource_type,
            actor_user_id=actor_user_id,
        )
    return [AuditEventResponse.from_entity(event) for event in events]


@router.get("/events", response_model=list[AuditEventResponse])
async def list_audit_events(
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    action: Annotated[str | None, Query(max_length=96)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    actor_user_id: UUID | None = None,
) -> list[AuditEventResponse]:
    return await _list_events(
        request=request,
        tenant_id=tenant_id,
        limit=limit,
        action=action,
        resource_type=resource_type,
        actor_user_id=actor_user_id,
    )


@router.get("/events/export")
async def export_audit_events(
    request: Request,
    tenant_id: TenantHeader = None,
    format: Literal["jsonl"] = "jsonl",
    limit: Annotated[int, Query(ge=1, le=5_000)] = 1_000,
) -> Response:
    if format != "jsonl":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unsupported_audit_export_format", "message": "不支持的导出格式"},
        )
    events = await _list_events(
        request=request,
        tenant_id=tenant_id,
        limit=limit,
    )
    payload = "\n".join(event.model_dump_json() for event in events)
    if payload:
        payload += "\n"
    return Response(content=payload, media_type="application/x-ndjson")


@router.get("/events/integrity", response_model=AuditIntegrityResponse)
async def verify_audit_integrity(
    request: Request,
    tenant_id: TenantHeader = None,
) -> AuditIntegrityResponse:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.OPERATIONS_MANAGE,
        )
        verification = await SqlAlchemyAuditEventRepository(
            database_session
        ).verify_integrity(tenant_id=access.tenant.id)
    return AuditIntegrityResponse(
        valid=verification.valid,
        checked_events=verification.checked_events,
        first_invalid_sequence=verification.first_invalid_sequence,
    )
