from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from .errors import ToolExecutionError
from .models import (
    ArgumentSummary,
    AuditEventType,
    PolicyContext,
    PolicyDecision,
    ToolAuditEvent,
    ToolDefinition,
    ToolInvocation,
    ToolInvocationOutcome,
)
from .policy import evaluate_policy
from .ports import CredentialResolver, ToolAuditSink, ToolDefinitionResolver, ToolExecutor


class ToolGateway:
    def __init__(
        self,
        *,
        executor: ToolExecutor,
        definition_resolver: ToolDefinitionResolver,
        credential_resolver: CredentialResolver,
        audit_sink: ToolAuditSink,
    ) -> None:
        self._executor = executor
        self._definition_resolver = definition_resolver
        self._credential_resolver = credential_resolver
        self._audit_sink = audit_sink

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: PolicyContext,
    ) -> ToolInvocationOutcome:
        summary = _summarize_arguments(invocation.arguments)
        definition = await self._definition_resolver.resolve(
            tenant_id=invocation.tenant_id,
            tool_id=invocation.tool_id,
        )
        if definition is None:
            reason = "tool_not_found"
            await self._audit_sink.emit(
                _audit_event(
                    event_type=AuditEventType.REJECTED,
                    invocation=invocation,
                    definition=None,
                    argument_summary=summary,
                    reason=reason,
                )
            )
            return ToolInvocationOutcome(decision=PolicyDecision.DENY, reason=reason)

        policy = evaluate_policy(invocation, definition, context)
        if policy.decision is not PolicyDecision.ALLOW:
            event_type = (
                AuditEventType.APPROVAL_REQUIRED
                if policy.decision is PolicyDecision.REQUIRE_APPROVAL
                else AuditEventType.REJECTED
            )
            await self._audit_sink.emit(
                _audit_event(
                    event_type=event_type,
                    invocation=invocation,
                    definition=definition,
                    argument_summary=summary,
                    reason=policy.reason,
                )
            )
            return ToolInvocationOutcome(decision=policy.decision, reason=policy.reason)

        await self._audit_sink.emit(
            _audit_event(
                event_type=AuditEventType.STARTED,
                invocation=invocation,
                definition=definition,
                argument_summary=summary,
            )
        )
        execution_failed = False
        result: object | None = None
        try:
            credentials = await self._credential_resolver.resolve(
                tenant_id=invocation.tenant_id,
                references=definition.credential_references,
            )
            result = await self._executor.execute(
                definition=definition,
                arguments=invocation.arguments,
                credentials=credentials,
            )
        except Exception:
            execution_failed = True

        if execution_failed:
            await self._audit_sink.emit(
                _audit_event(
                    event_type=AuditEventType.COMPLETED,
                    invocation=invocation,
                    definition=definition,
                    argument_summary=summary,
                    succeeded=False,
                    reason="tool_execution_failed",
                )
            )
            raise ToolExecutionError("Tool execution failed")

        await self._audit_sink.emit(
            _audit_event(
                event_type=AuditEventType.COMPLETED,
                invocation=invocation,
                definition=definition,
                argument_summary=summary,
                succeeded=True,
            )
        )
        return ToolInvocationOutcome(decision=PolicyDecision.ALLOW, result=result)


def _audit_event(
    *,
    event_type: AuditEventType,
    invocation: ToolInvocation,
    definition: ToolDefinition | None,
    argument_summary: ArgumentSummary,
    reason: str | None = None,
    succeeded: bool | None = None,
) -> ToolAuditEvent:
    return ToolAuditEvent(
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        tenant_id=invocation.tenant_id,
        run_id=invocation.run_id,
        employee_id=invocation.employee_id,
        user_id=invocation.user_id,
        tool_id=invocation.tool_id,
        tool_name=invocation.tool_name,
        risk=definition.risk if definition is not None else None,
        argument_summary=argument_summary,
        reason=reason,
        succeeded=succeeded,
    )


def _summarize_arguments(arguments: Mapping[str, object]) -> ArgumentSummary:
    canonical = json.dumps(
        _canonicalize(arguments),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ArgumentSummary(
        keys=tuple(sorted(arguments)),
        sha256=hashlib.sha256(canonical).hexdigest(),
        size_bytes=len(canonical),
    )


def _canonicalize(value: object) -> object:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, str):
        return {"length": len(value), "type": "string"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, Mapping):
        return {
            "fields": {str(key): _canonicalize(item) for key, item in value.items()},
            "type": "object",
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return {
            "items": [_canonicalize(item) for item in value],
            "length": len(value),
            "type": "array",
        }
    return {"type": type(value).__qualname__}
