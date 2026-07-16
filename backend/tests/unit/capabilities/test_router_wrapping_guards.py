"""wrap_capability_router 的装配期守卫契约。

包装器会重建路由，只透传 path/methods/status_code/name；对暂不支持的
路由形态必须装配期 fail-fast，禁止静默丢弃元数据或产出损坏的响应。
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends

from agent_platform.api.dependencies.capabilities import wrap_capability_router


def _noop_dependency() -> None:  # pragma: no cover - 仅用于路由声明
    return None


def test_wrap_keeps_plain_sync_routes() -> None:
    router = APIRouter()

    @router.post("/things", status_code=201)
    def create_thing() -> dict[str, str]:
        return {"ok": "yes"}

    wrapped = wrap_capability_router(router)
    assert len(wrapped.routes) == 1


def test_wrap_rejects_async_endpoints_at_assembly_time() -> None:
    """async 端点经 run_in_threadpool 会返回未 await 的协程，必须装配期拒绝。"""

    router = APIRouter()

    @router.get("/things")
    async def list_things() -> dict[str, str]:  # pragma: no cover - 不应被调用
        return {"ok": "yes"}

    with pytest.raises(TypeError, match="async"):
        wrap_capability_router(router)


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
