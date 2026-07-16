from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.capabilities.manifest import CapabilityManifest
from agent_platform.capabilities.request_context import (
    CapabilityRequestContext,
    bind_capability_request_context,
    require_capability_request_context,
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
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


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
            session_factory=request.app.state.session_factory,
        )
        token = bind_capability_request_context(context)
        try:
            yield
        finally:
            reset_capability_request_context(token)
            if context.audit_events:
                # 审计必须在响应发出前由 endpoint 包装层落库（见
                # wrap_capability_router）；此处残留说明装配错误，禁止静默丢失。
                logger.error(
                    "capability_audit_events_left_unflushed",
                    extra={
                        "capability_id": manifest.capability_id,
                        "event_count": len(context.audit_events),
                    },
                )

    return capability_gate  # type: ignore[return-value]


def wrap_capability_router(router: APIRouter) -> APIRouter:
    """Rebuild a capability router so audit flush happens before the response.

    本版 FastAPI 的 yield 依赖 teardown 在响应发送之后执行，无法把审计失败
    转成 5xx；因此在 endpoint 层包装：业务成功 → flush 失败必须 500。
    """

    wrapped_router = APIRouter()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            raise TypeError("capability routers must only contain API routes")
        # 装配期 fail-fast：包装器重建路由只透传 path/methods/status_code/name，
        # 暂不支持的路由形态必须显式拒绝，禁止静默丢弃元数据或产出损坏响应。
        if iscoroutinefunction(route.endpoint):
            raise TypeError(
                f"capability route {route.path} uses an async endpoint; "
                "the audit wrapper only supports sync endpoints for now"
            )
        if route.dependencies:
            raise TypeError(
                f"capability route {route.path} declares per-route dependencies, "
                "which the audit wrapper would silently drop"
            )
        wrapped_router.add_api_route(
            route.path,
            _wrap_capability_endpoint(route.endpoint),
            methods=sorted(route.methods or set()),
            status_code=route.status_code,
            name=route.name,
        )
    return wrapped_router


def _wrap_capability_endpoint(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(endpoint)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            result = await run_in_threadpool(endpoint, *args, **kwargs)
        except BaseException:
            # 失败路径尽力落库已发生的审计事件，但不掩盖业务异常。
            with suppress(Exception):
                await flush_pending_capability_audit_events()
            raise
        try:
            await flush_pending_capability_audit_events()
        except Exception:
            logger.exception("capability_audit_flush_failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "capability_audit_flush_failed",
                    "message": "能力审计写入失败，操作结果不可确认",
                },
            ) from None
        return result

    return wrapped


async def flush_pending_capability_audit_events() -> None:
    context = require_capability_request_context()
    if not context.audit_events:
        return
    session_factory = cast(SessionFactory | None, context.session_factory)
    if session_factory is None:
        raise RuntimeError("capability request context has no session factory")
    async with session_factory() as database_session:
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
    context.audit_events.clear()
