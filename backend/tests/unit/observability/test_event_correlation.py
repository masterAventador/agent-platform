from uuid import uuid4

from opentelemetry.sdk.trace import TracerProvider

from agent_platform.platform.runs.events import EventType, PlatformEvent


def _event(payload: dict[str, object]) -> PlatformEvent:
    return PlatformEvent.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        run_id=uuid4(),
        sequence=1,
        event_type=EventType.RUN_STARTED,
        payload=payload,
    )


def test_platform_event_uses_active_trace_correlation_instead_of_caller_value() -> None:
    tracer = TracerProvider(shutdown_on_exit=False).get_tracer("event-correlation-test")

    with tracer.start_as_current_span("platform.run") as span:
        event = _event(
            {"correlation": {"trace_id": "spoofed", "span_id": "spoofed"}}
        )
        context = span.get_span_context()

    assert event.payload["correlation"] == {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def test_platform_event_drops_spoofed_correlation_without_active_trace() -> None:
    event = _event(
        {
            "status": "running",
            "correlation": {"trace_id": "spoofed", "span_id": "spoofed"},
        }
    )

    assert event.payload == {"status": "running"}
