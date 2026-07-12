from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from weakref import WeakSet

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Link, Status

from agent_platform.config import AppSettings
from agent_platform.observability.attributes import (
    AttributeValue,
    sanitize_span_attributes,
)

_FLUSH_TIMEOUT_MILLIS = 30_000
_SENSITIVE_HTTP_HEADERS = ["authorization", "cookie", "set-cookie"]
_INSTRUMENTED_FASTAPI_APPS: WeakSet[FastAPI] = WeakSet()


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

    def instrument_app(self, app: FastAPI) -> None:
        if self.providers is None or app in _INSTRUMENTED_FASTAPI_APPS:
            return
        self._instrumentors.fastapi.instrument_app(
            app,
            tracer_provider=self.providers.tracer_provider,
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
        self.providers.tracer_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS)
        self.providers.tracer_provider.shutdown()

    @staticmethod
    def _instrument_once(instrumentor: LibraryInstrumentor, **kwargs: Any) -> None:
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(**kwargs)


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
) -> Telemetry:
    selected_instrumentors = instrumentors or InstrumentorSet()
    if not settings.otel_enabled:
        return Telemetry(providers=None, instrumentors=selected_instrumentors)
    selected_providers = providers or _create_providers(
        settings,
        span_exporter=span_exporter,
    )
    return Telemetry(
        providers=selected_providers,
        instrumentors=selected_instrumentors,
    )


def _create_providers(
    settings: AppSettings,
    *,
    span_exporter: SpanExporter | None,
) -> TelemetryProviders:
    resource = telemetry_resource(settings)
    selected_span_exporter = span_exporter or OTLPSpanExporter(
        endpoint=settings.otel_otlp_endpoint,
        insecure=settings.otel_otlp_insecure,
    )
    tracer_provider = TracerProvider(
        resource=resource,
        shutdown_on_exit=False,
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            SanitizingSpanExporter(selected_span_exporter),
        )
    )
    return TelemetryProviders(tracer_provider=tracer_provider)


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
