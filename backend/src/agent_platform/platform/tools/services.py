from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from agent_platform.platform.tools.entities import (
    MAX_SYNC_CATALOG_TOOLS,
    DiscoveredTool,
    McpConnectionResult,
    McpConnectionStatus,
    McpServer,
    McpSyncRemovedEntry,
    McpSyncReport,
    McpTransport,
    Tool,
    ToolApprovalPolicy,
    ToolOrigin,
    ToolReference,
    ToolRiskLevel,
    ToolVersion,
    validate_approval_policy,
    validate_tool_input_schema,
)
from agent_platform.platform.tools.errors import (
    McpConnectionFailed,
    McpServerInUse,
    McpServerNotFound,
    ToolInUse,
    ToolNotFound,
    ToolVersionNotFound,
)
from agent_platform.platform.tools.ports import (
    McpConnectionProbe,
    ToolCredentialResolver,
    ToolRepository,
)

SYNC_REPORT_KEEP = 20
MAX_DISCOVERED_NAME_LENGTH = 128
MAX_DISCOVERED_DESCRIPTION_LENGTH = 2000
DISCOVERED_TOOL_DEFAULT_RISK = ToolRiskLevel.EXTERNAL


class ToolRegistryService:
    def __init__(
        self,
        repository: ToolRepository,
        *,
        connection_probe: McpConnectionProbe | None = None,
        credential_resolver: ToolCredentialResolver | None = None,
    ) -> None:
        self._repository = repository
        self._connection_probe = connection_probe
        self._credential_resolver = credential_resolver

    async def register_server(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        transport: McpTransport,
        endpoint: str | None,
        command: str | None,
        args: list[str],
        secret_reference: str | None,
        enabled: bool,
    ) -> McpServer:
        server = McpServer.create(
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            transport=transport,
            endpoint=endpoint,
            command=command,
            args=args,
            secret_reference=secret_reference,
            enabled=enabled,
        )
        await self._repository.add_server(server)
        return server

    async def get_server(self, *, tenant_id: UUID, server_id: UUID) -> McpServer:
        server = await self._repository.get_server(tenant_id=tenant_id, server_id=server_id)
        if server is None:
            raise McpServerNotFound
        return server

    async def list_servers(self, *, tenant_id: UUID) -> list[McpServer]:
        return await self._repository.list_servers(tenant_id=tenant_id)

    async def set_server_enabled(
        self, *, tenant_id: UUID, server_id: UUID, enabled: bool
    ) -> McpServer:
        server = await self.get_server(tenant_id=tenant_id, server_id=server_id)
        updated = server.set_enabled(enabled)
        await self._repository.update_server(updated)
        return updated

    async def update_server(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        name: str | None = None,
        endpoint: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        enabled: bool | None = None,
    ) -> McpServer:
        server = await self.get_server(tenant_id=tenant_id, server_id=server_id)
        updated = server.with_settings(
            name=name, endpoint=endpoint, command=command, args=args
        )
        if enabled is not None:
            updated = updated.set_enabled(enabled)
        await self._repository.update_server(updated)
        return updated

    async def set_server_secret_reference(
        self, *, tenant_id: UUID, server_id: UUID, secret_reference: str | None
    ) -> McpServer:
        server = await self.get_server(tenant_id=tenant_id, server_id=server_id)
        updated = server.with_secret_reference(secret_reference)
        await self._repository.update_server(updated)
        return updated

    async def delete_server(self, *, tenant_id: UUID, server_id: UUID) -> None:
        server = await self.get_server(tenant_id=tenant_id, server_id=server_id)
        tools = await self._repository.list_tools(tenant_id=tenant_id, server_id=server.id)
        references = await self._repository.list_tool_references(
            tenant_id=tenant_id, tool_ids=[tool.id for tool in tools]
        )
        if references:
            raise McpServerInUse(list(references))
        await self._repository.delete_server(tenant_id=tenant_id, server_id=server.id)

    async def register_tool(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        name: str,
        description: str,
        input_schema: dict[str, object],
        risk_level: ToolRiskLevel,
        enabled: bool,
        origin: ToolOrigin = ToolOrigin.MANUAL,
        approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.RISK_BASED,
    ) -> Tool:
        if await self._repository.get_server(tenant_id=tenant_id, server_id=server_id) is None:
            raise McpServerNotFound
        tool = Tool.create(
            tenant_id=tenant_id,
            server_id=server_id,
            name=name,
            description=description,
            input_schema=input_schema,
            risk_level=risk_level,
            enabled=enabled,
            origin=origin,
            approval_policy=approval_policy,
        )
        await self._repository.add_tool(tool)
        await self._repository.add_tool_version(tool.snapshot(change_source="initial"))
        return tool

    async def list_tools(
        self, *, tenant_id: UUID, server_id: UUID | None = None
    ) -> list[Tool]:
        return await self._repository.list_tools(tenant_id=tenant_id, server_id=server_id)

    async def get_tool(self, *, tenant_id: UUID, tool_id: UUID) -> Tool:
        tool = await self._repository.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if tool is None:
            raise ToolNotFound
        return tool

    async def set_tool_enabled(
        self, *, tenant_id: UUID, tool_id: UUID, enabled: bool
    ) -> Tool:
        return await self.update_tool(tenant_id=tenant_id, tool_id=tool_id, enabled=enabled)

    async def update_tool(
        self,
        *,
        tenant_id: UUID,
        tool_id: UUID,
        description: str | None = None,
        input_schema: dict[str, object] | None = None,
        risk_level: ToolRiskLevel | None = None,
        approval_policy: ToolApprovalPolicy | None = None,
        enabled: bool | None = None,
    ) -> Tool:
        tool = await self.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        next_risk = risk_level if risk_level is not None else tool.risk_level
        next_policy = approval_policy if approval_policy is not None else tool.approval_policy
        validate_approval_policy(next_risk, next_policy)
        if input_schema is not None:
            validate_tool_input_schema(input_schema)
        definition_changed = (
            (description is not None and description != tool.description)
            or (input_schema is not None and input_schema != tool.input_schema)
            or next_risk is not tool.risk_level
            or next_policy is not tool.approval_policy
        )
        if definition_changed:
            tool = tool.with_definition(
                description=description,
                input_schema=input_schema,
                risk_level=risk_level,
                approval_policy=approval_policy,
            )
            await self._repository.update_tool(tool)
            await self._repository.add_tool_version(tool.snapshot(change_source="update"))
        if enabled is not None and enabled != tool.enabled:
            tool = tool.set_enabled(enabled)
            await self._repository.update_tool(tool)
        return tool

    async def rollback_tool(
        self, *, tenant_id: UUID, tool_id: UUID, version: int
    ) -> Tool:
        tool = await self.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        snapshot = await self._repository.get_tool_version(
            tenant_id=tenant_id, tool_id=tool_id, version=version
        )
        if snapshot is None:
            raise ToolVersionNotFound
        restored = tool.with_definition(
            description=snapshot.description,
            input_schema=snapshot.input_schema,
            risk_level=snapshot.risk_level,
            approval_policy=snapshot.approval_policy,
        )
        await self._repository.update_tool(restored)
        await self._repository.add_tool_version(restored.snapshot(change_source="rollback"))
        return restored

    async def list_tool_versions(
        self, *, tenant_id: UUID, tool_id: UUID
    ) -> list[ToolVersion]:
        await self.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        return await self._repository.list_tool_versions(tenant_id=tenant_id, tool_id=tool_id)

    async def list_tool_references(
        self, *, tenant_id: UUID, tool_id: UUID
    ) -> list[ToolReference]:
        await self.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        return await self._repository.list_tool_references(
            tenant_id=tenant_id, tool_ids=[tool_id]
        )

    async def delete_tool(self, *, tenant_id: UUID, tool_id: UUID) -> None:
        tool = await self.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        references = await self._repository.list_tool_references(
            tenant_id=tenant_id, tool_ids=[tool.id]
        )
        if references:
            raise ToolInUse(list(references))
        await self._repository.delete_tool(tenant_id=tenant_id, tool_id=tool.id)

    async def required_available_tool(self, *, tenant_id: UUID, tool_id: UUID) -> Tool:
        tool = await self._repository.get_tool(tenant_id=tenant_id, tool_id=tool_id)
        if tool is None or not tool.enabled or tool.upstream_missing:
            raise ToolNotFound
        server = await self._repository.get_server(
            tenant_id=tenant_id, server_id=tool.server_id
        )
        if server is None or not server.enabled:
            raise ToolNotFound
        return tool

    async def test_server_connection(
        self, *, tenant_id: UUID, server_id: UUID
    ) -> McpConnectionResult:
        server = await self.get_server(tenant_id=tenant_id, server_id=server_id)
        now = datetime.now(UTC)
        try:
            catalog = await self._probe_catalog(server)
        except McpConnectionFailed as failure:
            result = McpConnectionResult(
                status=McpConnectionStatus.FAILED,
                tested_at=now,
                error_code=failure.code,
            )
        else:
            result = McpConnectionResult(
                status=McpConnectionStatus.OK,
                tested_at=now,
                tool_count=len(catalog),
            )
        await self._repository.update_server(
            server.with_connection_result(
                status=result.status,
                error_code=result.error_code,
                tested_at=now,
            )
        )
        return result

    async def sync_server(self, *, tenant_id: UUID, server_id: UUID) -> McpSyncReport:
        server = await self.get_server(tenant_id=tenant_id, server_id=server_id)
        now = datetime.now(UTC)
        try:
            discovered = await self._probe_catalog(server)
            catalog = self._validated_catalog(discovered)
        except McpConnectionFailed as failure:
            await self._record_sync_failure(server, code=failure.code, occurred_at=now)
            raise

        locked = await self._repository.get_server_for_update(
            tenant_id=tenant_id, server_id=server_id
        )
        if locked is None:
            raise McpServerNotFound
        existing = {
            tool.name: tool
            for tool in await self._repository.list_tools(
                tenant_id=tenant_id, server_id=server_id
            )
        }
        added: list[str] = []
        updated: list[str] = []
        removed: list[McpSyncRemovedEntry] = []
        unchanged = 0

        for name in sorted(catalog):
            upstream = catalog[name]
            current = existing.get(name)
            if current is None:
                tool = Tool.create(
                    tenant_id=tenant_id,
                    server_id=server_id,
                    name=name,
                    description=upstream.description,
                    input_schema=upstream.input_schema,
                    risk_level=DISCOVERED_TOOL_DEFAULT_RISK,
                    enabled=False,
                    origin=ToolOrigin.DISCOVERED,
                    approval_policy=ToolApprovalPolicy.RISK_BASED,
                )
                await self._repository.add_tool(tool)
                await self._repository.add_tool_version(tool.snapshot(change_source="sync"))
                added.append(name)
                continue
            if current.origin is ToolOrigin.MANUAL:
                # 同名 MANUAL 工具是管理员手写资产：上游目录不覆盖其定义，
                # 也不参与 upstream_missing 标记，冲突静默跳过并计入未变化。
                unchanged += 1
                continue
            definition_changed = (
                current.description != upstream.description
                or current.input_schema != upstream.input_schema
            )
            if definition_changed:
                tool = current.with_definition(
                    description=upstream.description,
                    input_schema=upstream.input_schema,
                )
                if tool.upstream_missing:
                    tool = tool.mark_upstream_missing(missing=False, at=now)
                await self._repository.update_tool(tool)
                await self._repository.add_tool_version(tool.snapshot(change_source="sync"))
                updated.append(name)
            elif current.upstream_missing:
                await self._repository.update_tool(
                    current.mark_upstream_missing(missing=False, at=now)
                )
                updated.append(name)
            else:
                unchanged += 1

        for name in sorted(existing):
            current = existing[name]
            if name in catalog or current.origin is not ToolOrigin.DISCOVERED:
                continue
            if current.upstream_missing:
                continue
            references = await self._repository.list_tool_references(
                tenant_id=tenant_id, tool_ids=[current.id]
            )
            await self._repository.update_tool(
                current.mark_upstream_missing(missing=True, at=now)
            )
            removed.append(McpSyncRemovedEntry(name=name, referenced=bool(references)))

        report = McpSyncReport(
            id=uuid4(),
            tenant_id=tenant_id,
            server_id=server_id,
            occurred_at=now,
            status="ok",
            added=added,
            updated=updated,
            removed=removed,
            unchanged=unchanged,
        )
        await self._repository.add_sync_report(report, keep=SYNC_REPORT_KEEP)
        await self._repository.update_server(
            locked.with_connection_result(
                status=McpConnectionStatus.OK, error_code=None, tested_at=now
            ).with_synced_at(now)
        )
        return report

    async def list_sync_reports(
        self, *, tenant_id: UUID, server_id: UUID, limit: int = 20
    ) -> list[McpSyncReport]:
        await self.get_server(tenant_id=tenant_id, server_id=server_id)
        return await self._repository.list_sync_reports(
            tenant_id=tenant_id, server_id=server_id, limit=limit
        )

    async def _probe_catalog(self, server: McpServer) -> list[DiscoveredTool]:
        if self._connection_probe is None:
            raise McpConnectionFailed("mcp_probe_not_configured")
        credentials = await self._resolve_server_credentials(server)
        return await self._connection_probe.list_tools(
            server=server, credentials=credentials
        )

    async def _resolve_server_credentials(self, server: McpServer) -> Mapping[str, str]:
        if server.secret_reference is None:
            return {}
        if self._credential_resolver is None:
            raise McpConnectionFailed("credential_unavailable")
        try:
            return await self._credential_resolver.resolve(
                tenant_id=server.tenant_id,
                references=[server.secret_reference],
            )
        except Exception:
            raise McpConnectionFailed("credential_unavailable") from None

    async def _record_sync_failure(
        self, server: McpServer, *, code: str, occurred_at: datetime
    ) -> None:
        report = McpSyncReport(
            id=uuid4(),
            tenant_id=server.tenant_id,
            server_id=server.id,
            occurred_at=occurred_at,
            status="failed",
            error_code=code,
        )
        await self._repository.add_sync_report(report, keep=SYNC_REPORT_KEEP)
        await self._repository.update_server(
            server.with_connection_result(
                status=McpConnectionStatus.FAILED,
                error_code=code,
                tested_at=occurred_at,
            )
        )

    @staticmethod
    def _validated_catalog(
        discovered: list[DiscoveredTool],
    ) -> dict[str, DiscoveredTool]:
        if len(discovered) > MAX_SYNC_CATALOG_TOOLS:
            raise McpConnectionFailed("mcp_catalog_too_large")
        catalog: dict[str, DiscoveredTool] = {}
        for item in discovered:
            if (
                not item.name
                or len(item.name) > MAX_DISCOVERED_NAME_LENGTH
                or item.name in catalog
            ):
                raise McpConnectionFailed("mcp_invalid_catalog")
            description = item.description or ""
            if len(description) > MAX_DISCOVERED_DESCRIPTION_LENGTH:
                description = description[:MAX_DISCOVERED_DESCRIPTION_LENGTH]
            try:
                validate_tool_input_schema(item.input_schema)
            except Exception:
                raise McpConnectionFailed("mcp_invalid_catalog") from None
            catalog[item.name] = DiscoveredTool(
                name=item.name,
                description=description,
                input_schema=item.input_schema,
            )
        return catalog
