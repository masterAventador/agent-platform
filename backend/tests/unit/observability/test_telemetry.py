from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry._logs import LogRecord
from opentelemetry.sdk._logs import ReadableLogRecord
from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceFlags

from agent_platform.api.app import create_app
from agent_platform.config import AppSettings
from agent_platform.observability.telemetry import (
    InstrumentorSet,
    SanitizingLogExporter,
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


class RecordingMeterProvider(MeterProvider):
    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)
        self.flush_count = 0
        self.shutdown_count = 0

    def force_flush(self, timeout_millis: float = 30_000) -> bool:
        self.flush_count += 1
        return True

    def shutdown(self, timeout_millis: float = 30_000) -> None:
        self.shutdown_count += 1


class RecordingLoggerProvider(LoggerProvider):
    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)
        self.flush_count = 0
        self.shutdown_count = 0

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self.flush_count += 1
        return True

    def shutdown(self) -> None:
        self.shutdown_count += 1


class RecordingCounter:
    def __init__(self) -> None:
        self.add_calls: list[tuple[int, dict[str, str | int]]] = []

    def add(self, amount: int, attributes: dict[str, str | int] | None = None) -> None:
        self.add_calls.append((amount, attributes or {}))


class RecordingHistogram:
    def __init__(self) -> None:
        self.record_calls: list[tuple[float, dict[str, str | int]]] = []

    def record(self, amount: float, attributes: dict[str, str | int] | None = None) -> None:
        self.record_calls.append((amount, attributes or {}))


class RecordingMeter:
    def __init__(self) -> None:
        self.counter = RecordingCounter()
        self.histogram = RecordingHistogram()

    def create_counter(self, *args: object, **kwargs: object) -> RecordingCounter:
        return self.counter

    def create_histogram(self, *args: object, **kwargs: object) -> RecordingHistogram:
        return self.histogram


class RecordingApiMetricProvider(RecordingMeterProvider):
    def __init__(self) -> None:
        super().__init__()
        self.meter = RecordingMeter()

    def get_meter(self, *args: object, **kwargs: object) -> RecordingMeter:
        return self.meter


class RecordingSpanExporter(SpanExporter):
    def __init__(self) -> None:
        self.exported: tuple[ReadableSpan, ...] = ()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.exported = tuple(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class RecordingLogExporter(LogRecordExporter):
    def __init__(self) -> None:
        self.exported: tuple[ReadableLogRecord, ...] = ()

    def export(self, batch: Sequence[ReadableLogRecord]) -> LogRecordExportResult:
        self.exported = tuple(batch)
        return LogRecordExportResult.SUCCESS

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
    assert isinstance(telemetry.providers.meter_provider, MeterProvider)
    assert isinstance(telemetry.providers.logger_provider, LoggerProvider)

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
        meter_provider=RecordingMeterProvider(),
        logger_provider=RecordingLoggerProvider(),
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
    meter_provider = RecordingMeterProvider()
    logger_provider = RecordingLoggerProvider()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=TelemetryProviders(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
        ),
        instrumentors=make_instrumentors()[0],
    )

    telemetry.shutdown()
    telemetry.shutdown()

    assert tracer_provider.flush_count == 1
    assert tracer_provider.shutdown_count == 1
    assert meter_provider.flush_count == 1
    assert meter_provider.shutdown_count == 1
    assert logger_provider.flush_count == 1
    assert logger_provider.shutdown_count == 1


def test_instrument_app_records_basic_api_metrics_without_query_or_body() -> None:
    meter_provider = RecordingApiMetricProvider()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=TelemetryProviders(
            tracer_provider=RecordingTracerProvider(),
            meter_provider=cast(Any, meter_provider),
            logger_provider=RecordingLoggerProvider(),
        ),
        instrumentors=make_instrumentors()[0],
    )
    app = FastAPI()

    @app.post("/items/{item_id}")
    async def create_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    telemetry.instrument_app(app)

    response = TestClient(app).post(
        "/items/visible-id?token=must-not-enter-metrics",
        json={"password": "must-not-enter-metrics"},
    )

    assert response.status_code == 200
    assert meter_provider.meter.counter.add_calls == [
        (
            1,
            {
                "http.request.method": "POST",
                "http.route": "/items/{item_id}",
                "http.response.status_code": 200,
            },
        )
    ]
    assert meter_provider.meter.histogram.record_calls[0][1] == {
        "http.request.method": "POST",
        "http.route": "/items/{item_id}",
        "http.response.status_code": 200,
    }
    rendered = repr(meter_provider.meter.counter.add_calls)
    assert "must-not-enter-metrics" not in rendered


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


def test_sanitizing_log_exporter_redacts_body_and_sensitive_attributes() -> None:
    downstream = RecordingLogExporter()
    exporter = SanitizingLogExporter(downstream)
    record = ReadableLogRecord(
        log_record=LogRecord(
            severity_text="ERROR",
            body="request failed token=must-not-enter-logs",
            attributes={
                "authorization": "Bearer must-not-enter-logs",
                "safe.attribute": "kept",
                "exception.stacktrace": "password=must-not-enter-logs",
            },
        ),
        resource=Resource.create({"service.name": "test"}),
    )

    result = exporter.export((record,))

    assert result is LogRecordExportResult.SUCCESS
    exported = downstream.exported[0]
    assert exported.log_record.body == "request failed token=[redacted]"
    assert dict(exported.log_record.attributes or {}) == {"safe.attribute": "kept"}
    assert "must-not-enter-logs" not in exported.to_json()


def test_instrument_libraries_attaches_and_shutdown_removes_otel_logging_handler() -> None:
    providers = TelemetryProviders(
        tracer_provider=RecordingTracerProvider(),
        meter_provider=RecordingMeterProvider(),
        logger_provider=RecordingLoggerProvider(),
    )
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=providers,
        instrumentors=make_instrumentors()[0],
    )
    application_logger = logging.getLogger("agent_platform")
    before = tuple(application_logger.handlers)

    telemetry.instrument_libraries()
    attached = tuple(handler for handler in application_logger.handlers if handler not in before)

    assert len(attached) == 1
    telemetry.shutdown()
    assert tuple(application_logger.handlers) == before


@pytest.mark.asyncio
async def test_create_app_lifespan_shuts_down_injected_telemetry() -> None:
    tracer_provider = RecordingTracerProvider()
    telemetry = configure_telemetry(
        AppSettings(otel_enabled=True),
        providers=TelemetryProviders(
            tracer_provider=tracer_provider,
            meter_provider=RecordingMeterProvider(),
            logger_provider=RecordingLoggerProvider(),
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
