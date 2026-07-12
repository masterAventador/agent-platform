from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from re import compile as compile_pattern
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

type AttributeValue = str | bool | int | float | tuple[str, ...]
type Identifier = UUID | str


class PlatformOperation(StrEnum):
    RUN = "run"
    SKILL = "skill"
    TOOL = "tool"
    MCP = "mcp"
    KNOWLEDGE = "knowledge"


_SPAN_NAMES: Mapping[PlatformOperation, str] = MappingProxyType(
    {
        PlatformOperation.RUN: "platform.run",
        PlatformOperation.SKILL: "platform.skill",
        PlatformOperation.TOOL: "platform.tool",
        PlatformOperation.MCP: "platform.mcp",
        PlatformOperation.KNOWLEDGE: "platform.knowledge",
    }
)

_SENSITIVE_ATTRIBUTE_PARTS = frozenset(
    {
        "authorization",
        "body",
        "cookie",
        "header",
        "headers",
    }
)
_SENSITIVE_GEN_AI_PARTS = frozenset(
    {
        "arguments",
        "completion",
        "content",
        "input",
        "output",
        "prompt",
        "result",
    }
)
_DROPPED_ATTRIBUTE_NAMES = frozenset(
    {
        "db.query.text",
        "db.statement",
        "error.message",
        "exception.message",
        "exception.stacktrace",
        "exception.type",
        "http.target",
        "url.query",
    }
)
_URL_ATTRIBUTE_NAMES = frozenset({"http.url", "url.full"})
_BOUNDED_ERROR_TYPES = frozenset(
    {
        "cancelled",
        "conflict",
        "internal",
        "invalid_argument",
        "not_found",
        "permission_denied",
        "timeout",
        "unavailable",
    }
)
_ERROR_CODE_PATTERN = compile_pattern(r"[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?")


def span_name(operation: PlatformOperation) -> str:
    return _SPAN_NAMES[operation]


def platform_span_attributes(
    *,
    tenant_id: Identifier | None = None,
    run_id: Identifier | None = None,
    employee_id: Identifier | None = None,
    skill_id: Identifier | None = None,
    tool_id: Identifier | None = None,
    mcp_server_id: Identifier | None = None,
    knowledge_base_id: Identifier | None = None,
) -> dict[str, AttributeValue]:
    """Build attributes from an explicit non-content correlation allowlist."""
    candidates = {
        "platform.tenant.id": tenant_id,
        "platform.run.id": run_id,
        "platform.employee.id": employee_id,
        "platform.skill.id": skill_id,
        "platform.tool.id": tool_id,
        "platform.mcp.server.id": mcp_server_id,
        "platform.knowledge_base.id": knowledge_base_id,
    }
    return {name: str(value) for name, value in candidates.items() if value is not None}


def sanitize_span_attributes(
    attributes: Mapping[str, AttributeValue],
) -> dict[str, AttributeValue]:
    """Return export-safe auto-instrumentation attributes.

    This pure seam is intended for use by an SDK SpanProcessor before export.
    """
    sanitized: dict[str, AttributeValue] = {}
    for name, value in attributes.items():
        normalized_name = name.lower().replace("-", "_")
        parts = frozenset(normalized_name.split("."))
        if normalized_name in _DROPPED_ATTRIBUTE_NAMES:
            continue
        if parts & _SENSITIVE_ATTRIBUTE_PARTS:
            continue
        if normalized_name.startswith("gen_ai.") and parts & _SENSITIVE_GEN_AI_PARTS:
            continue
        if normalized_name == "error.type" and value not in _BOUNDED_ERROR_TYPES:
            continue
        if normalized_name == "error.code" and (
            not isinstance(value, str) or _ERROR_CODE_PATTERN.fullmatch(value) is None
        ):
            continue
        if normalized_name in _URL_ATTRIBUTE_NAMES:
            if not isinstance(value, str):
                continue
            safe_url = _sanitize_url(value)
            if safe_url is None:
                continue
            sanitized[name] = safe_url
            continue
        sanitized[name] = value
    return sanitized


def _sanitize_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.netloc and hostname is None:
        return None
    safe_netloc = _safe_netloc(hostname, port)
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", ""))


def _safe_netloc(hostname: str | None, port: int | None) -> str:
    if hostname is None:
        return ""
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    rendered_port = f":{port}" if port is not None else ""
    return f"{rendered_host}{rendered_port}"
