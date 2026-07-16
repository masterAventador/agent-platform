from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, status

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.capabilities.manifest import CapabilityManifest
from agent_platform.capabilities.request_context import (
    CapabilityRequestContext,
    bind_capability_request_context,
    reset_capability_request_context,
)
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.entitlements import (
    SqlAlchemyCapabilityEntitlementRepository,
)
from agent_platform.platform.entitlements.services import evaluate_capability_availability
from agent_platform.platform.tenants.memberships import WorkspaceAccess
from agent_platform.platform.users.entities import User

logger = logging.getLogger(__name__)

CapabilityGateDependency = Callable[[Request], AsyncIterator[None]]


def parse_tenant_header(request: Request) -> UUID | None:
    raw_tenant_id = request.headers.get("X-Tenant-ID")
    if raw_tenant_id is None:
        return None
    try:
        return UUID(raw_tenant_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_tenant_id", "message": "租户标识格式不正确"},
        ) from None


async def resolve_capability_actor(
    *,
    request: Request,
    manifest: CapabilityManifest,
) -> tuple[User, WorkspaceAccess, frozenset[str]]:
    """Authenticate and enforce `installed && entitled && permitted` server-side."""

    tenant_id = parse_tenant_header(request)
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=None,
        )
        entitlement = await SqlAlchemyCapabilityEntitlementRepository(database_session).get(
            tenant_id=access.tenant.id,
            capability_id=manifest.capability_id,
        )
    availability = evaluate_capability_availability(
        deployment_installed=True,
        entitlement=entitlement,
        role=access.role,
        manifest_permissions=manifest.permissions,
        now=datetime.now(UTC),
    )
    if not availability.tenant_entitled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "capability_not_entitled", "message": "当前工作区未获此能力授权"},
        )
    if not availability.user_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "permission_denied", "message": "没有执行此操作的权限"},
        )
    return user, access, availability.user_permissions


async def capability_permissions_for_workspaces(
    *,
    request: Request,
    database_session: Any,
    workspaces: list[WorkspaceAccess],
) -> dict[UUID, list[str]]:
    """Per-tenant capability permission codes for auth responses (server-trimmed)."""

    host = request.app.state.capability_host
    manifests = host.installed_manifests
    if not manifests:
        return {}
    repository = SqlAlchemyCapabilityEntitlementRepository(database_session)
    now = datetime.now(UTC)
    permissions: dict[UUID, list[str]] = {}
    for access in workspaces:
        granted: set[str] = set()
        for manifest in manifests:
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
            granted |= availability.user_permissions
        if granted:
            permissions[access.tenant.id] = sorted(granted)
    return permissions


def create_capability_gate(
    manifest: CapabilityManifest,
) -> Callable[[Request], Coroutine[Any, Any, Any]]:
    async def capability_gate(request: Request) -> AsyncIterator[None]:
        user, access, permissions = await resolve_capability_actor(
            request=request,
            manifest=manifest,
        )
        context = CapabilityRequestContext(
            capability_id=manifest.capability_id,
            tenant_id=access.tenant.id,
            user_id=user.id,
            permissions=permissions,
        )
        token = bind_capability_request_context(context)
        endpoint_failed = False
        try:
            yield
        except BaseException:
            endpoint_failed = True
            raise
        finally:
            reset_capability_request_context(token)
            try:
                await _flush_capability_audit_events(request, context)
            except Exception:
                logger.exception(
                    "capability_audit_flush_failed",
                    extra={"capability_id": manifest.capability_id},
                )
                if not endpoint_failed:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={
                            "code": "capability_audit_flush_failed",
                            "message": "能力审计写入失败，操作结果不可确认",
                        },
                    ) from None

    return capability_gate  # type: ignore[return-value]


async def _flush_capability_audit_events(
    request: Request,
    context: CapabilityRequestContext,
) -> None:
    if not context.audit_events:
        return
    async with request.app.state.session_factory() as database_session:
        for event in context.audit_events:
            if event.tenant_id != context.tenant_id:
                raise RuntimeError("capability audit event crossed tenant boundary")
            await emit_audit_event(
                database_session,
                tenant_id=event.tenant_id,
                actor_user_id=event.actor_user_id,
                action=event.action,
                resource_type=context.capability_id,
                resource_id=event.resource_id,
                metadata={key: value for key, value in event.details},
            )
        await database_session.commit()
