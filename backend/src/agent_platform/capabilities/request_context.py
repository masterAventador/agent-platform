from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from agent_platform.capabilities.registry import CapabilityHostError


@runtime_checkable
class CapabilityAuditEvent(Protocol):
    """Structured capability audit record bridged into the Core audit protocol."""

    @property
    def event_id(self) -> UUID: ...

    @property
    def action(self) -> str: ...

    @property
    def tenant_id(self) -> UUID: ...

    @property
    def actor_user_id(self) -> UUID: ...

    @property
    def resource_id(self) -> UUID: ...

    @property
    def occurred_at(self) -> datetime: ...

    @property
    def details(self) -> tuple[tuple[str, str], ...]: ...


@dataclass(slots=True)
class CapabilityRequestContext:
    """Per-request actor context established by the server-side capability gate."""

    capability_id: str
    tenant_id: UUID
    user_id: UUID
    permissions: frozenset[str]
    session_factory: object = None
    audit_events: list[CapabilityAuditEvent] = field(default_factory=list)


_current_context: ContextVar[CapabilityRequestContext | None] = ContextVar(
    "capability_request_context",
    default=None,
)


def bind_capability_request_context(context: CapabilityRequestContext) -> object:
    return _current_context.set(context)


def reset_capability_request_context(token: object) -> None:
    _current_context.reset(token)  # type: ignore[arg-type]


def require_capability_request_context() -> CapabilityRequestContext:
    context = _current_context.get()
    if context is None:
        raise CapabilityHostError("capability request context is not bound")
    return context


class ContextBufferAuditSink:
    """AuditPort adapter that buffers events on the bound request context."""

    def record(self, event: CapabilityAuditEvent) -> None:
        require_capability_request_context().audit_events.append(event)
