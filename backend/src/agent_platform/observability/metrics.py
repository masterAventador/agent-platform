from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol


class Counter(Protocol):
    def add(self, amount: int, attributes: dict[str, str] | None = None) -> None: ...


class Histogram(Protocol):
    def record(self, amount: float, attributes: dict[str, str] | None = None) -> None: ...


class Meter(Protocol):
    def create_counter(
        self,
        name: str,
        unit: str = "",
        description: str = "",
    ) -> Counter: ...

    def create_histogram(
        self,
        name: str,
        unit: str = "",
        description: str = "",
        *,
        explicit_bucket_boundaries_advisory: Sequence[float] | None = None,
    ) -> Histogram: ...


class OperationalComponent(StrEnum):
    WORKER = "worker"
    QUEUE = "queue"
    MODEL_GATEWAY = "model_gateway"
    RAGFLOW = "ragflow"
    SANDBOX = "sandbox"
    CLIENT = "client"
    AUDIT = "audit"


_OPERATIONS: dict[OperationalComponent, frozenset[str]] = {
    OperationalComponent.WORKER: frozenset({"run", "recovery", "heartbeat"}),
    OperationalComponent.QUEUE: frozenset(
        {"setup", "enqueue", "dequeue", "ack", "reclaim", "dead_letter"}
    ),
    OperationalComponent.MODEL_GATEWAY: frozenset({"readiness", "chat"}),
    OperationalComponent.RAGFLOW: frozenset(
        {
            "health",
            "create_dataset",
            "delete_dataset",
            "upload_document",
            "parse_document",
            "list_chunks",
            "retrieve",
        }
    ),
    OperationalComponent.SANDBOX: frozenset(
        {"acquire", "reconnect", "delete", "heartbeat", "file", "command"}
    ),
    OperationalComponent.CLIENT: frozenset({"page", "interaction", "api", "sse", "error"}),
    OperationalComponent.AUDIT: frozenset({"persist", "verify", "retention"}),
}
_OUTCOMES = frozenset({"succeeded", "failed", "denied", "timeout"})
_COUNTER_NAMES: dict[OperationalComponent, str] = {
    OperationalComponent.WORKER: "agent_platform.worker.operations",
    OperationalComponent.QUEUE: "agent_platform.queue.operations",
    OperationalComponent.MODEL_GATEWAY: "agent_platform.model_gateway.requests",
    OperationalComponent.RAGFLOW: "agent_platform.ragflow.requests",
    OperationalComponent.SANDBOX: "agent_platform.sandbox.operations",
    OperationalComponent.CLIENT: "agent_platform.client.events",
    OperationalComponent.AUDIT: "agent_platform.audit.events",
}


class OperationalMetrics:
    """Fixed-name, bounded-label metrics shared by API and worker adapters."""

    def __init__(self, meter: Meter) -> None:
        self._counters = {
            component: meter.create_counter(name, unit="1")
            for component, name in _COUNTER_NAMES.items()
        }
        self._durations = {
            component: meter.create_histogram(
                f"agent_platform.{component.value}.operation.duration",
                unit="ms",
            )
            for component in OperationalComponent
        }
        self._worker_failures = meter.create_counter(
            "agent_platform.worker.runs.failed",
            unit="1",
        )
        self._queue_dead_letters = meter.create_counter(
            "agent_platform.queue.dead_letters",
            unit="1",
        )
        self._client_errors = meter.create_counter(
            "agent_platform.client.errors",
            unit="1",
        )
        self._audit_failures = meter.create_counter(
            "agent_platform.audit.events.failed",
            unit="1",
        )

    def record(
        self,
        *,
        component: OperationalComponent,
        operation: str,
        outcome: str,
        duration_ms: float,
    ) -> None:
        if operation not in _OPERATIONS[component]:
            raise ValueError("unsupported operational metric operation")
        if outcome not in _OUTCOMES:
            raise ValueError("unsupported operational metric outcome")
        attributes = {"operation": operation, "outcome": outcome}
        self._counters[component].add(1, attributes)
        self._durations[component].record(max(0.0, duration_ms), attributes)
        if component is OperationalComponent.WORKER and operation == "run" and outcome == "failed":
            self._worker_failures.add(1, {})
        if component is OperationalComponent.QUEUE and operation == "dead_letter":
            self._queue_dead_letters.add(1, {"outcome": outcome})
        if component is OperationalComponent.CLIENT and (
            operation == "error" or outcome == "failed"
        ):
            self._client_errors.add(1, {"operation": operation})
        if component is OperationalComponent.AUDIT and outcome == "failed":
            self._audit_failures.add(1, {"operation": operation})
