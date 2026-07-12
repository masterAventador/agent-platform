from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

import pytest

from agent_platform.observability.attributes import (
    PlatformOperation,
    platform_span_attributes,
    sanitize_span_attributes,
    span_name,
)
from agent_platform.observability.spans import (
    current_trace_correlation,
    platform_span,
    with_trace_correlation,
)


@dataclass
class FakeSpanContext:
    trace_id: int
    span_id: int
    is_valid: bool = True


@dataclass
class FakeSpan:
    context: FakeSpanContext
    attributes: dict[str, object] = field(default_factory=dict)

    def set_attribute(self, name: str, value: object) -> None:
        self.attributes[name] = value

    def get_span_context(self) -> FakeSpanContext:
        return self.context


@dataclass
class FakeTracer:
    span: FakeSpan
    name: str | None = None
    attributes: dict[str, object] = field(default_factory=dict)
    record_exception: bool | None = None
    set_status_on_exception: bool | None = None

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> Iterator[FakeSpan]:
        self.name = name
        self.attributes = dict(attributes or {})
        self.record_exception = record_exception
        self.set_status_on_exception = set_status_on_exception
        yield self.span


class ToolErrorCode(StrEnum):
    UPSTREAM_TIMEOUT = "upstream_timeout"


def test_operation_names_are_stable_and_low_cardinality() -> None:
    assert {operation: span_name(operation) for operation in PlatformOperation} == {
        PlatformOperation.RUN: "platform.run",
        PlatformOperation.SKILL: "platform.skill",
        PlatformOperation.TOOL: "platform.tool",
        PlatformOperation.MCP: "platform.mcp",
        PlatformOperation.KNOWLEDGE: "platform.knowledge",
    }


def test_platform_attributes_only_accept_explicit_safe_identifiers() -> None:
    tenant_id = uuid4()
    run_id = uuid4()

    attributes = platform_span_attributes(
        tenant_id=tenant_id,
        run_id=run_id,
        employee_id="employee-1",
        tool_id="tool-1",
    )

    assert attributes == {
        "platform.tenant.id": str(tenant_id),
        "platform.run.id": str(run_id),
        "platform.employee.id": "employee-1",
        "platform.tool.id": "tool-1",
    }
    rendered = repr(attributes)
    for forbidden in ("prompt", "arguments", "credential", "input", "output", "content"):
        assert forbidden not in rendered


def test_sanitizer_removes_sensitive_auto_instrumentation_attributes() -> None:
    sanitized = sanitize_span_attributes(
        {
            "http.request.method": "POST",
            "http.request.header.authorization": ("Bearer secret",),
            "http.response.headers": "private headers",
            "http.request.body": "private body",
            "http.request.header.cookie": "session=secret",
            "db.system.name": "postgresql",
            "db.statement": "SELECT * FROM secrets",
            "gen_ai.request.model": "safe-model-name",
            "gen_ai.prompt.0.content": "private prompt",
            "gen_ai.input.messages": "private input",
            "gen_ai.output.messages": "private output",
            "gen_ai.response.content": "private content",
        }
    )

    assert sanitized == {
        "http.request.method": "POST",
        "db.system.name": "postgresql",
        "gen_ai.request.model": "safe-model-name",
    }


def test_sanitizer_strips_url_userinfo_query_and_fragment() -> None:
    sanitized = sanitize_span_attributes(
        {
            "url.full": "https://alice:password@example.com:8443/items/42?token=secret#private",
            "server.address": "example.com",
        }
    )

    assert sanitized == {
        "url.full": "https://example.com:8443/items/42",
        "server.address": "example.com",
    }


def test_sanitizer_handles_legacy_urls_database_queries_and_error_details() -> None:
    sanitized = sanitize_span_attributes(
        {
            "http.url": "https://alice:password@example.com/items?token=secret#private",
            "url.query": "token=secret",
            "http.target": "/items?token=secret",
            "db.query.text": "SELECT secret FROM credentials",
            "exception.type": "TimeoutError",
            "exception.message": "private prompt",
            "exception.stacktrace": "private stack",
            "error.type": "timeout",
            "error.code": "upstream_timeout",
            "error.message": "private model output",
        }
    )

    assert sanitized == {
        "http.url": "https://example.com/items",
        "error.type": "timeout",
        "error.code": "upstream_timeout",
    }


def test_sanitizer_drops_unbounded_error_type_and_code_values() -> None:
    assert sanitize_span_attributes(
        {
            "error.type": "customer prompt must not escape",
            "error.code": "UPSTREAM TIMEOUT: private details",
        }
    ) == {}


def test_sanitizer_removes_gen_ai_tool_arguments_results_and_call_content() -> None:
    sanitized = sanitize_span_attributes(
        {
            "gen_ai.request.model": "safe-model-name",
            "gen_ai.tool.call.arguments": "private arguments",
            "gen_ai.tool.call.result": "private result",
            "gen_ai.tool_call.content": "private tool call",
        }
    )

    assert sanitized == {"gen_ai.request.model": "safe-model-name"}


def test_platform_span_disables_framework_exception_capture_and_records_stable_error() -> None:
    secret = "customer prompt must not escape"
    fake_span = FakeSpan(FakeSpanContext(trace_id=1, span_id=2))
    tracer = FakeTracer(fake_span)

    with (
        pytest.raises(TimeoutError, match=secret),
        platform_span(
            tracer,
            PlatformOperation.TOOL,
            attributes={"platform.tool.id": "tool-1"},
            error_code=ToolErrorCode.UPSTREAM_TIMEOUT,
        ),
    ):
        raise TimeoutError(secret)

    assert tracer.name == "platform.tool"
    assert tracer.attributes == {"platform.tool.id": "tool-1"}
    assert tracer.record_exception is False
    assert tracer.set_status_on_exception is False
    assert fake_span.attributes == {
        "error.type": "timeout",
        "error.code": "upstream_timeout",
    }
    assert secret not in repr(fake_span.attributes)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "timeout"),
        (PermissionError(), "permission_denied"),
        (ValueError(), "invalid_argument"),
        (RuntimeError(), "internal"),
    ],
)
def test_platform_span_maps_exceptions_to_bounded_error_types(
    error: Exception,
    expected: str,
) -> None:
    fake_span = FakeSpan(FakeSpanContext(trace_id=1, span_id=2))

    with (
        pytest.raises(type(error)),
        platform_span(FakeTracer(fake_span), PlatformOperation.RUN),
    ):
        raise error

    assert fake_span.attributes == {"error.type": expected}


def test_current_trace_correlation_uses_fixed_width_hex_ids() -> None:
    span = FakeSpan(FakeSpanContext(trace_id=0xA1, span_id=0xB2))

    assert current_trace_correlation(span) == {
        "trace_id": "000000000000000000000000000000a1",
        "span_id": "00000000000000b2",
    }


@pytest.mark.parametrize(
    "span",
    [None, FakeSpan(FakeSpanContext(trace_id=0, span_id=0, is_valid=False))],
)
def test_current_trace_correlation_is_empty_without_an_active_valid_span(
    span: FakeSpan | None,
) -> None:
    assert current_trace_correlation(span) == {}


def test_trace_correlation_is_added_to_a_copy_of_platform_event_payload() -> None:
    payload = {"status": "running"}
    span = FakeSpan(FakeSpanContext(trace_id=0xA1, span_id=0xB2))

    correlated = with_trace_correlation(payload, span)

    assert correlated == {
        "status": "running",
        "correlation": {
            "trace_id": "000000000000000000000000000000a1",
            "span_id": "00000000000000b2",
        },
    }
    assert payload == {"status": "running"}


def test_trace_correlation_cannot_be_spoofed_without_an_active_span() -> None:
    payload = {
        "status": "running",
        "correlation": {"trace_id": "spoofed", "span_id": "spoofed"},
    }

    assert with_trace_correlation(payload, None) == {"status": "running"}
