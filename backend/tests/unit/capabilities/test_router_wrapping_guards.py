"""wrap_capability_router 的装配期守卫契约。

包装器会重建路由，只透传 path/methods/status_code/name；对暂不支持的
路由形态必须装配期 fail-fast，禁止静默丢弃元数据或产出损坏的响应。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

import agent_platform.api.dependencies.capabilities as capability_dependencies
from agent_platform.api.dependencies.capabilities import wrap_capability_router
from agent_platform.capabilities.request_context import (
    CapabilityRequestContext,
    bind_capability_request_context,
    require_capability_request_context,
    reset_capability_request_context,
)


def _noop_dependency() -> None:  # pragma: no cover - 仅用于路由声明
    return None


@dataclass(frozen=True, slots=True)
class _AuditEvent:
    event_id: UUID
    action: str
    tenant_id: UUID
    actor_user_id: UUID
    resource_id: UUID
    occurred_at: datetime
    details: tuple[tuple[str, str], ...] = ()


class _FakeSession:
    async def commit(self) -> None:
        return None


@asynccontextmanager
async def _fake_session_factory():
    yield _FakeSession()


@dataclass(slots=True)
class _ContextProbe:
    tenant_id: UUID = field(default_factory=uuid4)
    user_id: UUID = field(default_factory=uuid4)

    def build(self) -> CapabilityRequestContext:
        return CapabilityRequestContext(
            capability_id="video-studio",
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            permissions=frozenset({"video.read"}),
            session_factory=_fake_session_factory,
        )


def _build_gated_app(router: APIRouter, probe: _ContextProbe) -> FastAPI:
    async def bind_context():
        context = probe.build()
        token = bind_capability_request_context(context)
        try:
            yield
        finally:
            reset_capability_request_context(token)

    app = FastAPI()
    app.include_router(wrap_capability_router(router), dependencies=[Depends(bind_context)])
    return app


def test_wrap_keeps_plain_sync_routes() -> None:
    router = APIRouter()

    @router.post("/things", status_code=201)
    def create_thing() -> dict[str, str]:
        return {"ok": "yes"}

    wrapped = wrap_capability_router(router)
    assert len(wrapped.routes) == 1


def test_wrap_supports_async_endpoints_and_flushes_audit_before_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B04 能力路由是 async 端点：包装器必须 await 后在响应前落库审计。"""

    emitted: list[str] = []

    async def recording_emit(session: object, **kwargs: object) -> None:
        emitted.append(str(kwargs["action"]))

    monkeypatch.setattr(capability_dependencies, "emit_audit_event", recording_emit)

    router = APIRouter()
    probe = _ContextProbe()

    @router.post("/things", status_code=201)
    async def create_thing() -> dict[str, str]:
        context = require_capability_request_context()
        context.audit_events.append(
            _AuditEvent(
                event_id=uuid4(),
                action="video.material.created",
                tenant_id=probe.tenant_id,
                actor_user_id=probe.user_id,
                resource_id=uuid4(),
                occurred_at=datetime.now(UTC),
            )
        )
        return {"ok": "yes"}

    client = TestClient(_build_gated_app(router, probe))
    response = client.post("/things")
    assert response.status_code == 201
    assert response.json() == {"ok": "yes"}
    assert emitted == ["video.material.created"]


def test_wrap_async_endpoint_returns_500_when_audit_flush_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async 端点业务成功但审计桥接失败时必须显式 500，禁止静默丢审计。"""

    async def broken_emit(session: object, **kwargs: object) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(capability_dependencies, "emit_audit_event", broken_emit)

    router = APIRouter()
    probe = _ContextProbe()

    @router.post("/things", status_code=201)
    async def create_thing() -> dict[str, str]:
        context = require_capability_request_context()
        context.audit_events.append(
            _AuditEvent(
                event_id=uuid4(),
                action="video.material.created",
                tenant_id=probe.tenant_id,
                actor_user_id=probe.user_id,
                resource_id=uuid4(),
                occurred_at=datetime.now(UTC),
            )
        )
        return {"ok": "yes"}

    client = TestClient(_build_gated_app(router, probe), raise_server_exceptions=False)
    response = client.post("/things")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "capability_audit_flush_failed"


def test_wrap_rejects_per_route_dependencies() -> None:
    """逐路由 dependencies 不透传即静默丢失鉴权，必须装配期拒绝。"""

    router = APIRouter()

    @router.get("/things", dependencies=[Depends(_noop_dependency)])
    def list_things() -> dict[str, str]:  # pragma: no cover - 不应被调用
        return {"ok": "yes"}

    with pytest.raises(TypeError, match="dependencies"):
        wrap_capability_router(router)


def test_wrap_preserves_annotation_inferred_response_model() -> None:
    """@wraps 透传 __annotations__，重建路由按同一注解重新推断 response_model。"""

    from fastapi.routing import APIRoute

    router = APIRouter()

    @router.get("/things")
    def list_things() -> dict[str, str]:  # pragma: no cover - 仅装配
        return {"ok": "yes"}

    original = router.routes[0]
    wrapped = wrap_capability_router(router).routes[0]
    assert isinstance(original, APIRoute) and isinstance(wrapped, APIRoute)
    assert wrapped.response_model == original.response_model
