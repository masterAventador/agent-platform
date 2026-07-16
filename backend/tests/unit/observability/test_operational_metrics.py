from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent_platform.observability.metrics import OperationalComponent, OperationalMetrics


@dataclass
class RecordingInstrument:
    calls: list[tuple[float, dict[str, str]]] = field(default_factory=list)

    def add(self, value: int, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((value, attributes or {}))

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((value, attributes or {}))


class RecordingMeter:
    def __init__(self) -> None:
        self.instruments: dict[str, RecordingInstrument] = {}

    def create_counter(self, name: str, **_: object) -> RecordingInstrument:
        return self.instruments.setdefault(name, RecordingInstrument())

    def create_histogram(self, name: str, **_: object) -> RecordingInstrument:
        return self.instruments.setdefault(name, RecordingInstrument())


def test_operational_metrics_cover_c14_domains_with_low_cardinality_labels() -> None:
    meter = RecordingMeter()
    metrics = OperationalMetrics(meter)  # type: ignore[arg-type]

    # AUDIT 组件禁止在此直接构造终态制造覆盖假象：
    # 其成功/失败计数必须经真实仓储写入路径断言，见 test_audit_metrics.py。
    examples = {
        OperationalComponent.WORKER: "run",
        OperationalComponent.QUEUE: "enqueue",
        OperationalComponent.MODEL_GATEWAY: "readiness",
        OperationalComponent.RAGFLOW: "retrieve",
        OperationalComponent.SANDBOX: "acquire",
        OperationalComponent.CLIENT: "api",
    }
    for component, operation in examples.items():
        metrics.record(
            component=component,
            operation=operation,
            outcome="failed",
            duration_ms=12.5,
        )

    assert {
        "agent_platform.worker.operations",
        "agent_platform.queue.operations",
        "agent_platform.model_gateway.requests",
        "agent_platform.ragflow.requests",
        "agent_platform.sandbox.operations",
        "agent_platform.client.events",
        "agent_platform.audit.events",
        "agent_platform.worker.runs.failed",
        "agent_platform.queue.dead_letters",
        "agent_platform.client.errors",
        "agent_platform.audit.events.failed",
    }.issubset(meter.instruments)
    rendered = repr(
        [call for instrument in meter.instruments.values() for call in instrument.calls]
    )
    assert "tenant" not in rendered
    assert "run_id" not in rendered
    assert "token" not in rendered


def test_operational_metrics_reject_unbounded_operation_and_outcome() -> None:
    metrics = OperationalMetrics(RecordingMeter())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported operational metric operation"):
        metrics.record(
            component=OperationalComponent.QUEUE,
            operation="token=must-not-enter-metrics",
            outcome="failed",
            duration_ms=1,
        )
    with pytest.raises(ValueError, match="unsupported operational metric outcome"):
        metrics.record(
            component=OperationalComponent.QUEUE,
            operation="enqueue",
            outcome="tenant-specific-outcome",
            duration_ms=1,
        )
