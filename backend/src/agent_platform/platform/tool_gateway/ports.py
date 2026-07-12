from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from .models import ToolAuditEvent, ToolDefinition


class ToolDefinitionResolver(Protocol):
    async def resolve(
        self, *, tenant_id: UUID, tool_id: UUID
    ) -> ToolDefinition | None: ...


class CredentialResolver(Protocol):
    async def resolve(
        self, *, tenant_id: UUID, references: Sequence[str]
    ) -> Mapping[str, str]: ...


class ToolExecutor(Protocol):
    async def execute(
        self,
        *,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        credentials: Mapping[str, str],
        invocation_id: UUID | None,
    ) -> object: ...


class ToolAuditSink(Protocol):
    async def emit(self, event: ToolAuditEvent) -> None: ...
