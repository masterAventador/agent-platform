from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol, cast
from weakref import WeakSet

from fastapi import FastAPI, Request
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry._logs import LogRecord
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, ReadableLogRecord
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Link, Status
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from agent_platform.config import AppSettings
from agent_platform.observability.attributes import (
    AttributeValue,
    sanitize_span_attributes,
)
from agent_platform.observability.correlation import current_correlation_id

_FLUSH_TIMEOUT_MILLIS = 30_000
_SENSITIVE_HTTP_HEADERS = ["authorization", "cookie", "set-cookie"]
_INSTRUMENTED_FASTAPI_APPS: WeakSet[FastAPI] = WeakSet()
_APPLICATION_LOGGER = logging.getLogger("agent_platform")
_LOG_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential)"
    r"\s*[:=]\s*[^\s,;&]+"
)


class LibraryInstrumentor(Protocol):
    @property
    def is_instrumented_by_opentelemetry(self) -> bool: ...

    def instrument(self, **kwargs: Any) -> None: ...


class FastAPIAppInstrumentor(Protocol):
    def instrument_app(self, app: FastAPI, **kwargs: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class InstrumentorSet:
    fastapi: FastAPIAppInstrumentor = field(default_factory=FastAPIInstrumentor)
    sqlalchemy: LibraryInstrumentor = field(default_factory=SQLAlchemyInstrumentor)
    httpx: LibraryInstrumentor = field(default_factory=HTTPXClientInstrumentor)
    redis: LibraryInstrumentor = field(default_factory=RedisInstrumentor)


@dataclass(frozen=True, slots=True)
class TelemetryProviders:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider


class SanitizingSpanExporter(SpanExporter):
    """Pass an immutable, sanitized span view to the configured exporter."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export(tuple(_sanitized_span(span) for span in spans))

    def force_flush(self, timeout_millis: int = _FLUSH_TIMEOUT_MILLIS) -> bool:
        return self._exporter.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._exporter.shutdown()


class SanitizingLogExporter(LogRecordExporter):
    """Export an immutable log view without raw content or sensitive attributes."""

    def __init__(self, exporter: LogRecordExporter) -> None:
        self._exporter = exporter

    def export(self, batch: Sequence[ReadableLogRecord]):  # type: ignore[no-untyped-def]
        return self._exporter.export(tuple(_sanitized_log_record(record) for record in batch))

    def shutdown(self) -> None:
        self._exporter.shutdown()


class _CorrelationLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        correlation_id = current_correlation_id()
        if correlation_id is not None:
            record.correlation_id = correlation_id
        return True


class Telemetry:
    def __init__(
        self,
        *,
        providers: TelemetryProviders | None,
        instrumentors: InstrumentorSet,
    ) -> None:
        self.providers = providers
        self._instrumentors = instrumentors
        self._shutdown = False
        self._logging_handler: LoggingHandler | None = None

    def instrument_libraries(self) -> None:
        if self.providers is None:
            return
        tracer_provider = self.providers.tracer_provider
        self._instrument_once(
            self._instrumentors.sqlalchemy,
            tracer_provider=tracer_provider,
            enable_commenter=False,
            enable_attribute_commenter=False,
        )
        self._instrument_once(
            self._instrumentors.httpx,
            tracer_provider=tracer_provider,
        )
        self._instrument_once(
            self._instrumentors.redis,
            tracer_provider=tracer_provider,
        )
        self._instrument_logging()

    def instrument_app(self, app: FastAPI) -> None:
        if self.providers is None or app in _INSTRUMENTED_FASTAPI_APPS:
            return
        self._install_api_metrics(app)
        self._instrumentors.fastapi.instrument_app(
            app,
            tracer_provider=self.providers.tracer_provider,
            meter_provider=self.providers.meter_provider,
            http_capture_headers_server_request=[],
            http_capture_headers_server_response=[],
            http_capture_headers_sanitize_fields=_SENSITIVE_HTTP_HEADERS,
            exclude_spans=["receive", "send"],
        )
        _INSTRUMENTED_FASTAPI_APPS.add(app)

    def shutdown(self) -> None:
        if self.providers is None or self._shutdown:
            return
        self._shutdown = True
        if self._logging_handler is not None:
            _APPLICATION_LOGGER.removeHandler(self._logging_handler)
            self._logging_handler = None
        self.providers.tracer_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS)
        self.providers.meter_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS)
        self.providers.logger_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS)
        self.providers.tracer_provider.shutdown()
        self.providers.meter_provider.shutdown(timeout_millis=_FLUSH_TIMEOUT_MILLIS)
        self.providers.logger_provider.shutdown()

    @staticmethod
    def _instrument_once(instrumentor: LibraryInstrumentor, **kwargs: Any) -> None:
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(**kwargs)

    def _instrument_logging(self) -> None:
        if self.providers is None or self._logging_handler is not None:
            return
        handler = LoggingHandler(
            level=logging.NOTSET,
            logger_provider=self.providers.logger_provider,
        )
        handler.addFilter(_CorrelationLogFilter())
        _APPLICATION_LOGGER.addHandler(handler)
        self._logging_handler = handler

    def _install_api_metrics(self, app: FastAPI) -> None:
        if self.providers is None:
            return
        meter = self.providers.meter_provider.get_meter("agent_platform.api")
        request_counter = meter.create_counter(
            "agent_platform.api.server.requests",
            unit="1",
            description="Total number of handled API requests.",
        )
        duration_histogram = meter.create_histogram(
            "agent_platform.api.server.duration",
            unit="ms",
            description="API request duration in milliseconds.",
        )

        @app.middleware("http")
        async def record_api_metrics(
            request: Request,
            call_next: RequestResponseEndpoint,
        ) -> Response:
            start = perf_counter()
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                route = request.scope.get("route")
                route_path = getattr(route, "path", request.url.path)
                if not isinstance(route_path, str):
                    route_path = request.url.path
                attributes: dict[str, str | int] = {
                    "http.request.method": request.method,
                    "http.route": route_path,
                    "http.response.status_code": status_code,
                }
                request_counter.add(1, attributes)
                duration_histogram.record((perf_counter() - start) * 1000, attributes)


def telemetry_resource(settings: AppSettings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.otel_service_name,
            "deployment.environment.name": settings.otel_environment,
        }
    )


def configure_telemetry(
    settings: AppSettings,
    *,
    providers: TelemetryProviders | None = None,
    instrumentors: InstrumentorSet | None = None,
    span_exporter: SpanExporter | None = None,
    metric_exporter: MetricExporter | None = None,
    log_exporter: LogRecordExporter | None = None,
) -> Telemetry:
    selected_instrumentors = instrumentors or InstrumentorSet()
    if not settings.otel_enabled:
        return Telemetry(providers=None, instrumentors=selected_instrumentors)
    selected_providers = providers or _create_providers(
        settings,
        span_exporter=span_exporter,
        metric_exporter=metric_exporter,
        log_exporter=log_exporter,
    )
    return Telemetry(
        providers=selected_providers,
        instrumentors=selected_instrumentors,
    )


def _create_providers(
    settings: AppSettings,
    *,
    span_exporter: SpanExporter | None,
    metric_exporter: MetricExporter | None,
    log_exporter: LogRecordExporter | None,
) -> TelemetryProviders:
    resource = telemetry_resource(settings)
    selected_span_exporter = span_exporter or OTLPSpanExporter(
        endpoint=settings.otel_otlp_endpoint,
        insecure=settings.otel_otlp_insecure,
    )
    selected_metric_exporter = metric_exporter or OTLPMetricExporter(
        endpoint=settings.otel_otlp_endpoint,
        insecure=settings.otel_otlp_insecure,
    )
    selected_log_exporter = log_exporter or OTLPLogExporter(
        endpoint=settings.otel_otlp_endpoint,
        insecure=settings.otel_otlp_insecure,
    )
    tracer_provider = TracerProvider(
        resource=resource,
        shutdown_on_exit=False,
    )
    meter_provider = MeterProvider(
        metric_readers=[PeriodicExportingMetricReader(selected_metric_exporter)],
        resource=resource,
        shutdown_on_exit=False,
    )
    logger_provider = LoggerProvider(
        resource=resource,
        shutdown_on_exit=False,
        meter_provider=meter_provider,
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            SanitizingSpanExporter(selected_span_exporter),
        )
    )
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            SanitizingLogExporter(selected_log_exporter),
            meter_provider=meter_provider,
        )
    )
    return TelemetryProviders(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
    )


def _sanitized_span(span: ReadableSpan) -> ReadableSpan:
    attributes = sanitize_span_attributes(
        cast(Mapping[str, AttributeValue], span.attributes or {})
    )
    events = tuple(
        Event(
            event.name,
            sanitize_span_attributes(
                cast(Mapping[str, AttributeValue], event.attributes or {})
            ),
            event.timestamp,
        )
        for event in span.events
    )
    links = tuple(
        Link(
            link.context,
            sanitize_span_attributes(
                cast(Mapping[str, AttributeValue], link.attributes or {})
            ),
        )
        for link in span.links
    )
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=links,
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _sanitized_log_record(record: ReadableLogRecord) -> ReadableLogRecord:
    source = record.log_record
    body = source.body
    safe_body = (
        _LOG_SECRET_PATTERN.sub(
            lambda match: f"{match.group(1)}=[redacted]",
            body,
        )[:1_024]
        if isinstance(body, str)
        else "[non-string log body redacted]"
    )
    attributes = sanitize_span_attributes(
        cast(Mapping[str, AttributeValue], source.attributes or {})
    )
    return ReadableLogRecord(
        log_record=LogRecord(
            timestamp=source.timestamp,
            observed_timestamp=source.observed_timestamp,
            context=source.context,
            severity_text=source.severity_text,
            severity_number=source.severity_number,
            body=safe_body,
            attributes=attributes,
            event_name=source.event_name,
        ),
        resource=record.resource,
        instrumentation_scope=record.instrumentation_scope,
        limits=record.limits,
    )
