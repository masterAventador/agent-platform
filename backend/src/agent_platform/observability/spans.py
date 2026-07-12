from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue

from agent_platform.observability.attributes import (
    AttributeValue,
    PlatformOperation,
    span_name,
)


class PlatformSpanContext(Protocol):
    @property
    def trace_id(self) -> int: ...

    @property
    def span_id(self) -> int: ...

    @property
    def is_valid(self) -> bool: ...


class PlatformSpan(Protocol):
    def set_attribute(self, name: str, value: AttributeValue) -> None: ...

    def get_span_context(self) -> PlatformSpanContext: ...


class PlatformTracer(Protocol):
    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> AbstractContextManager[PlatformSpan]: ...


@contextmanager
def platform_span(
    tracer: PlatformTracer,
    operation: PlatformOperation,
    *,
    attributes: Mapping[str, AttributeValue] | None = None,
    error_code: StrEnum | None = None,
) -> Iterator[PlatformSpan]:
    """Start a safe platform span without framework exception payload capture."""
    if error_code is not None and not isinstance(error_code, StrEnum):
        raise TypeError("error_code must be a StrEnum member")
    with tracer.start_as_current_span(
        span_name(operation),
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as error:
            span.set_attribute("error.type", _error_type(error))
            if error_code is not None:
                span.set_attribute("error.code", error_code.value)
            raise


def current_trace_correlation(span: PlatformSpan | None) -> dict[str, str]:
    if span is None:
        return {}
    context = span.get_span_context()
    if (
        not context.is_valid
        or not 0 < context.trace_id < (1 << 128)
        or not 0 < context.span_id < (1 << 64)
    ):
        return {}
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def with_trace_correlation(
    payload: Mapping[str, JsonValue],
    span: PlatformSpan | None,
) -> dict[str, JsonValue]:
    correlated = {name: value for name, value in payload.items() if name != "correlation"}
    correlation_ids = current_trace_correlation(span)
    if correlation_ids:
        correlation: dict[str, JsonValue] = dict(correlation_ids)
        correlated["correlation"] = correlation
    return correlated


def _error_type(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, ValueError):
        return "invalid_argument"
    if isinstance(error, LookupError):
        return "not_found"
    if isinstance(error, ConnectionError):
        return "unavailable"
    return "internal"
