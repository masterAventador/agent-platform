from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceFlags

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.observability.telemetry import (
    InstrumentorSet,
    SanitizingSpanExporter,
    TelemetryProviders,
    configure_telemetry,
    telemetry_resource,
)


class RecordingInstrumentor:
    def __init__(self) -> None:
        self.is_instrumented_by_opentelemetry = False
        self.calls: list[dict[str, Any]] = []

    def instrument(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        self.is_instrumented_by_opentelemetry = True


class RecordingFastAPIInstrumentor:
    def __init__(self) -> None:
        self.calls: list[tuple[FastAPI, dict[str, Any]]] = []

    def instrument_app(self, app: FastAPI, **kwargs: Any) -> None:
        self.calls.append((app, kwargs))


class RecordingTracerProvider(TracerProvider):
    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)
        self.flush_count = 0
        self.shutdown_count = 0

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self.flush_count += 1
        return True

    def shutdown(self) -> None:
        self.shutdown_count += 1


class RecordingSpanExporter(SpanExporter):
    def __init__(self) -> None:
        self.exported: tuple[ReadableSpan, ...] = ()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.exported = tuple(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def make_instrumentors() -> tuple[InstrumentorSet, list[RecordingInstrumentor]]:
    fastapi = RecordingFastAPIInstrumentor()
    libraries = [RecordingInstrumentor() for _ in range(3)]
    instrumentors = InstrumentorSet(
        fastapi=cast(Any, fastapi),
        sqlalchemy=cast(Any, libraries[0]),
        httpx=cast(Any, libraries[1]),
        redis=cast(Any, libraries[2]),
    )
    return instrumentors, libraries


def test_telemetry_resource_uses_service_and_environment_settings() -> None:
    settings = AppSettings(
        otel_service_name="agent-platform-test",
        otel_environment="test",
    )

    resource = telemetry_resource(settings)

    assert resource.attributes["service.name"] == "agent-platform-test"
    assert resource.attributes["deployment.environment.name"] == "test"


def test_enabled_telemetry_builds_trace_provider_with_injected_exporter() -> None:
    telemetry = configure_telemetry(
        AppSettings(
            otel_enabled=True,
            otel_service_name="agent-platform-test",
            otel_environment="test",
        ),
        span_exporter=RecordingSpanExporter(),
    )

    assert telemetry.providers is not None
    resource = telemetry.providers.tracer_provider.resource
    assert resource.attributes["service.name"] == "agent-platform-test"
    assert resource.attributes["deployment.environment.name"] == "test"

    telemetry.shutdown()


def test_disabled_telemetry_is_a_noop() -> None:
    instrumentors, libraries = make_instrumentors()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=False),
        instrumentors=instrumentors,
    )
    app = FastAPI()

    telemetry.instrument_libraries()
    telemetry.instrument_app(app)
    telemetry.shutdown()

    assert telemetry.providers is None
    assert all(not instrumentor.calls for instrumentor in libraries)
    assert not cast(RecordingFastAPIInstrumentor, instrumentors.fastapi).calls


def test_instrumentation_is_idempotent_and_disables_sensitive_http_capture() -> None:
    instrumentors, libraries = make_instrumentors()
    providers = TelemetryProviders(
        tracer_provider=RecordingTracerProvider(),
    )
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=providers,
        instrumentors=instrumentors,
    )
    app = FastAPI()

    telemetry.instrument_libraries()
    telemetry.instrument_libraries()
    telemetry.instrument_app(app)
    telemetry.instrument_app(app)

    assert [len(instrumentor.calls) for instrumentor in libraries] == [1, 1, 1]
    fastapi_calls = cast(RecordingFastAPIInstrumentor, instrumentors.fastapi).calls
    assert len(fastapi_calls) == 1
    _, kwargs = fastapi_calls[0]
    assert kwargs["http_capture_headers_server_request"] == []
    assert kwargs["http_capture_headers_server_response"] == []
    assert kwargs["http_capture_headers_sanitize_fields"] == [
        "authorization",
        "cookie",
        "set-cookie",
    ]
    assert kwargs["exclude_spans"] == ["receive", "send"]


def test_shutdown_flushes_and_closes_injected_trace_provider_once() -> None:
    tracer_provider = RecordingTracerProvider()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=TelemetryProviders(
            tracer_provider=tracer_provider,
        ),
        instrumentors=make_instrumentors()[0],
    )

    telemetry.shutdown()
    telemetry.shutdown()

    assert tracer_provider.flush_count == 1
    assert tracer_provider.shutdown_count == 1


def test_sanitizing_exporter_removes_sensitive_auto_instrumentation_data() -> None:
    downstream = RecordingSpanExporter()
    exporter = SanitizingSpanExporter(downstream)
    span = ReadableSpan(
        "GET",
        attributes={
            "url.full": "https://user:secret@example.com/path?token=secret#fragment",
            "db.statement": "SELECT * FROM users WHERE password = 'secret'",
            "http.request.header.authorization": "Bearer secret",
            "http.request.header.cookie": "session=secret",
            "http.request.body": "secret body",
            "gen_ai.prompt": "secret prompt",
            "safe.attribute": "kept",
        },
        events=[
            Event(
                "request",
                {
                    "http.response.header.set_cookie": "session=secret",
                    "safe.event.attribute": "kept",
                },
            )
        ],
        links=[
            Link(
                SpanContext(
                    trace_id=1,
                    span_id=1,
                    is_remote=False,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                ),
                {
                    "authorization": "Bearer secret",
                    "safe.link.attribute": "kept",
                },
            )
        ],
        status=Status(StatusCode.ERROR, "raw exception message"),
    )

    result = exporter.export((span,))

    assert result is SpanExportResult.SUCCESS
    exported = downstream.exported[0]
    assert exported.attributes == {
        "url.full": "https://example.com/path",
        "safe.attribute": "kept",
    }
    assert exported.events[0].attributes == {"safe.event.attribute": "kept"}
    assert exported.links[0].attributes == {"safe.link.attribute": "kept"}
    assert exported.status.status_code is StatusCode.ERROR
    assert exported.status.description is None


@pytest.mark.asyncio
async def test_create_app_lifespan_shuts_down_injected_telemetry() -> None:
    tracer_provider = RecordingTracerProvider()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=TelemetryProviders(
            tracer_provider=tracer_provider,
        ),
        instrumentors=make_instrumentors()[0],
    )
    app = create_app(
        settings=AppSettings(
            database_url="sqlite+aiosqlite://",
            otel_enabled=True,
        ),
        telemetry=telemetry,
    )

    async with app.router.lifespan_context(app):
        assert app.state.telemetry is telemetry

    assert tracer_provider.flush_count == 1
    assert tracer_provider.shutdown_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("otel_service_name", "api"),
        ("otel_environment", "production"),
        ("otel_otlp_endpoint", "collector:4317"),
        ("otel_otlp_insecure", False),
        ("otel_enabled", True),
    ],
)
def test_settings_expose_telemetry_configuration(field: str, value: object) -> None:
    settings = AppSettings(**{field: value})

    assert getattr(settings, field) == value
