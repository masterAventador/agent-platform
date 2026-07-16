from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.entitlements import (
    SqlAlchemyCapabilityEntitlementRepository,
)
from agent_platform.platform.entitlements.entities import (
    CapabilityEntitlement,
    EntitlementValidationError,
    validate_entitlement_source,
    validate_expiry,
)
from agent_platform.platform.entitlements.services import evaluate_capability_availability
from agent_platform.platform.tenants.permissions import TenantPermission

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]

_REGISTRY_SCHEMA_VERSION = "1.0"


class CapabilityRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    deployment_installed: bool
    tenant_entitled: bool
    frontend_entries: list[str] | None = None
    permissions: list[str] | None = None


class CapabilityRegistryResponse(BaseModel):
    schema_version: str
    capabilities: list[CapabilityRegistryEntry]


class EntitlementResponse(BaseModel):
    capability_id: str
    status: str
    source: str
    expires_at: datetime | None
    granted_at: datetime
    granted_by: UUID | None
    revoked_at: datetime | None
    revoked_by: UUID | None

    @classmethod
    def from_entity(cls, entitlement: CapabilityEntitlement) -> EntitlementResponse:
        return cls(
            capability_id=entitlement.capability_id,
            status=entitlement.status.value,
            source=entitlement.source,
            expires_at=entitlement.expires_at,
            granted_at=entitlement.granted_at,
            granted_by=entitlement.granted_by,
            revoked_at=entitlement.revoked_at,
            revoked_by=entitlement.revoked_by,
        )


class GrantEntitlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "manual"
    expires_at: datetime | None = None


@router.get(
    "/registry",
    response_model=CapabilityRegistryResponse,
    response_model_exclude_none=True,
)
async def capability_registry(
    request: Request,
    tenant_id: TenantHeader = None,
) -> CapabilityRegistryResponse:
    host = request.app.state.capability_host
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=None,
        )
        repository = SqlAlchemyCapabilityEntitlementRepository(database_session)
        now = datetime.now(UTC)
        entries: list[CapabilityRegistryEntry] = []
        for manifest in host.installed_manifests:
            entitlement = await repository.get(
                tenant_id=access.tenant.id,
                capability_id=manifest.capability_id,
            )
            availability = evaluate_capability_availability(
                deployment_installed=True,
                entitlement=entitlement,
                role=access.role,
                manifest_permissions=manifest.permissions,
                now=now,
            )
            if availability.available:
                entries.append(
                    CapabilityRegistryEntry(
                        capability_id=manifest.capability_id,
                        deployment_installed=True,
                        tenant_entitled=True,
                        frontend_entries=list(manifest.frontend_entries),
                        permissions=list(manifest.permissions),
                    )
                )
            else:
                entries.append(
                    CapabilityRegistryEntry(
                        capability_id=manifest.capability_id,
                        deployment_installed=True,
                        tenant_entitled=availability.tenant_entitled,
                    )
                )
    return CapabilityRegistryResponse(
        schema_version=_REGISTRY_SCHEMA_VERSION,
        capabilities=entries,
    )


@router.get("/entitlements", response_model=list[EntitlementResponse])
async def list_entitlements(
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[EntitlementResponse]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        entitlements = await SqlAlchemyCapabilityEntitlementRepository(
            database_session
        ).list_for_tenant(tenant_id=access.tenant.id)
    return [EntitlementResponse.from_entity(entitlement) for entitlement in entitlements]


@router.put("/entitlements/{capability_id}", response_model=EntitlementResponse)
async def grant_entitlement(
    capability_id: str,
    payload: GrantEntitlementRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> EntitlementResponse:
    _require_known_capability(request, capability_id)
    _require_installed_capability(request, capability_id)
    now = datetime.now(UTC)
    try:
        validate_entitlement_source(payload.source)
        validate_expiry(payload.expires_at, now=now)
    except EntitlementValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_entitlement_request", "message": str(error)},
        ) from error

    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        repository = SqlAlchemyCapabilityEntitlementRepository(database_session)
        previous = await repository.get(
            tenant_id=access.tenant.id,
            capability_id=capability_id,
        )
        entitlement = await repository.grant(
            tenant_id=access.tenant.id,
            capability_id=capability_id,
            granted_by=user.id,
            source=payload.source,
            expires_at=payload.expires_at,
            now=now,
        )
        await emit_audit_event(
            database_session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="entitlement.granted",
            resource_type="capability_entitlement",
            resource_id=entitlement.id,
            metadata={
                "capability_id": capability_id,
                "source": payload.source,
                "expires_at": (
                    payload.expires_at.isoformat() if payload.expires_at is not None else None
                ),
                "previous_status": previous.status.value if previous is not None else None,
            },
        )
        await database_session.commit()
    return EntitlementResponse.from_entity(entitlement)


@router.delete("/entitlements/{capability_id}", response_model=EntitlementResponse)
async def revoke_entitlement(
    capability_id: str,
    request: Request,
    tenant_id: TenantHeader = None,
) -> EntitlementResponse:
    _require_known_capability(request, capability_id)
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.WORKSPACE_MANAGE,
        )
        repository = SqlAlchemyCapabilityEntitlementRepository(database_session)
        entitlement = await repository.revoke(
            tenant_id=access.tenant.id,
            capability_id=capability_id,
            revoked_by=user.id,
            now=datetime.now(UTC),
        )
        if entitlement is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "entitlement_not_found", "message": "该能力尚未授权"},
            )
        await emit_audit_event(
            database_session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="entitlement.revoked",
            resource_type="capability_entitlement",
            resource_id=entitlement.id,
            metadata={"capability_id": capability_id},
        )
        await database_session.commit()
    return EntitlementResponse.from_entity(entitlement)


def _require_known_capability(request: Request, capability_id: str) -> None:
    catalog = request.app.state.capability_catalog
    if capability_id not in catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "capability_unknown", "message": "未知能力"},
        )


def _require_installed_capability(request: Request, capability_id: str) -> None:
    host = request.app.state.capability_host
    if capability_id not in host.installed_capability_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "capability_not_installed",
                "message": "当前部署未安装此能力，无法授予",
            },
        )
