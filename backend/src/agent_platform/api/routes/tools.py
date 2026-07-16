import re
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import (
    SqlAlchemyToolAuditReader,
    emit_audit_event,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.infrastructure.secrets import LocalCredentialConfigurationError
from agent_platform.platform.tenants.permissions import TenantPermission
from agent_platform.platform.tools.entities import (
    McpConnectionResult,
    McpServer,
    McpSyncReport,
    McpTransport,
    Tool,
    ToolApprovalPolicy,
    ToolOrigin,
    ToolReference,
    ToolRiskLevel,
    ToolVersion,
)
from agent_platform.platform.tools.errors import (
    InvalidApprovalPolicy,
    InvalidToolSchema,
    McpConnectionFailed,
    McpServerInUse,
    McpServerNotFound,
    RegistryNameAlreadyExists,
    ToolInUse,
    ToolNotFound,
    ToolVersionNotFound,
)
from agent_platform.platform.tools.services import ToolRegistryService

TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
CommandArgument = Annotated[str, Field(max_length=500)]
mcp_router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers"])
tool_router = APIRouter(prefix="/api/v1/tools", tags=["tools"])
invocation_router = APIRouter(prefix="/api/v1/tool-invocations", tags=["tools"])

MANAGED_SECRET_REFERENCE_PREFIX = "local://mcp-servers/"
_CREDENTIAL_KEY_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: McpTransport
    endpoint: HttpUrl | None = None
    command: str | None = Field(default=None, min_length=1, max_length=500)
    args: list[CommandArgument] = Field(default_factory=list, max_length=100)
    secret_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        pattern=r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$",
    )
    enabled: bool = True

    @model_validator(mode="after")
    def validate_transport_configuration(self) -> Self:
        if self.transport is McpTransport.STREAMABLE_HTTP:
            if self.endpoint is None or self.command is not None or self.args:
                raise ValueError("streamable_http transport requires only endpoint")
            _validate_endpoint(self.endpoint)
        elif self.command is None or self.endpoint is not None:
            raise ValueError("stdio transport requires command and does not accept endpoint")
        return self


def _validate_endpoint(endpoint: HttpUrl) -> None:
    if endpoint.username is not None or endpoint.password is not None:
        raise ValueError("endpoint must not contain credentials")
    if endpoint.query is not None or endpoint.fragment is not None:
        raise ValueError("endpoint must not contain query parameters or fragments")


class McpServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    endpoint: HttpUrl | None = None
    command: str | None = Field(default=None, min_length=1, max_length=500)
    args: list[CommandArgument] | None = Field(default=None, max_length=100)
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_endpoint_shape(self) -> Self:
        if self.endpoint is not None:
            _validate_endpoint(self.endpoint)
        return self


class McpServerResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    transport: McpTransport
    endpoint: str | None
    command: str | None
    args: list[str]
    enabled: bool
    has_credentials: bool
    connection_status: str
    connection_tested_at: datetime | None
    connection_error_code: str | None
    last_synced_at: datetime | None

    @classmethod
    def from_entity(cls, server: McpServer) -> "McpServerResponse":
        return cls(
            id=server.id,
            tenant_id=server.tenant_id,
            name=server.name,
            transport=server.transport,
            endpoint=server.endpoint,
            command=server.command,
            args=server.args,
            enabled=server.enabled,
            has_credentials=server.secret_reference is not None,
            connection_status=server.connection_status.value,
            connection_tested_at=server.connection_tested_at,
            connection_error_code=server.connection_error_code,
            last_synced_at=server.last_synced_at,
        )


class McpServerCredentialsUpdate(BaseModel):
    values: dict[str, str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        for key, value in self.values.items():
            if len(key) > 256 or _CREDENTIAL_KEY_PATTERN.fullmatch(key) is None:
                raise ValueError("credential keys must be valid HTTP token characters")
            if (
                not value
                or len(value) > 4096
                or any(character in value for character in ("\r", "\n", "\x00"))
            ):
                raise ValueError("credential values must be single-line non-empty strings")
        return self


class ConnectionTestResponse(BaseModel):
    status: str
    tested_at: datetime
    tool_count: int | None = None
    error_code: str | None = None

    @classmethod
    def from_entity(cls, result: McpConnectionResult) -> "ConnectionTestResponse":
        return cls(
            status=result.status.value,
            tested_at=result.tested_at,
            tool_count=result.tool_count,
            error_code=result.error_code,
        )


class SyncRemovedEntryResponse(BaseModel):
    name: str
    referenced: bool


class SyncReportResponse(BaseModel):
    id: UUID
    server_id: UUID
    occurred_at: datetime
    status: str
    added: list[str]
    updated: list[str]
    removed: list[SyncRemovedEntryResponse]
    unchanged: int
    error_code: str | None

    @classmethod
    def from_entity(cls, report: McpSyncReport) -> "SyncReportResponse":
        return cls(
            id=report.id,
            server_id=report.server_id,
            occurred_at=report.occurred_at,
            status=report.status,
            added=list(report.added),
            updated=list(report.updated),
            removed=[
                SyncRemovedEntryResponse(name=entry.name, referenced=entry.referenced)
                for entry in report.removed
            ],
            unchanged=report.unchanged,
            error_code=report.error_code,
        )


class ToolCreate(BaseModel):
    server_id: UUID
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel = ToolRiskLevel.READ
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.RISK_BASED
    enabled: bool = True


class ToolUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    input_schema: dict[str, object] | None = None
    risk_level: ToolRiskLevel | None = None
    approval_policy: ToolApprovalPolicy | None = None
    enabled: bool | None = None


class ToolRollbackRequest(BaseModel):
    version: int = Field(ge=1)


class ToolResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    server_id: UUID
    name: str
    description: str
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel
    approval_policy: ToolApprovalPolicy
    origin: ToolOrigin
    upstream_missing: bool
    version: int
    enabled: bool

    @classmethod
    def from_entity(cls, tool: Tool) -> "ToolResponse":
        return cls(
            id=tool.id,
            tenant_id=tool.tenant_id,
            server_id=tool.server_id,
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            risk_level=tool.risk_level,
            approval_policy=tool.approval_policy,
            origin=tool.origin,
            upstream_missing=tool.upstream_missing,
            version=tool.version,
            enabled=tool.enabled,
        )


class ToolVersionResponse(BaseModel):
    version: int
    description: str
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel
    approval_policy: ToolApprovalPolicy
    change_source: str
    created_at: datetime

    @classmethod
    def from_entity(cls, item: ToolVersion) -> "ToolVersionResponse":
        return cls(
            version=item.version,
            description=item.description,
            input_schema=item.input_schema,
            risk_level=item.risk_level,
            approval_policy=item.approval_policy,
            change_source=item.change_source,
            created_at=item.created_at,
        )


class ToolReferenceResponse(BaseModel):
    tool_id: UUID
    employee_id: UUID
    employee_name: str
    relation: str
    version: int | None

    @classmethod
    def from_entity(cls, item: ToolReference) -> "ToolReferenceResponse":
        return cls(
            tool_id=item.tool_id,
            employee_id=item.employee_id,
            employee_name=item.employee_name,
            relation=item.relation,
            version=item.version,
        )


class ToolInvocationResponse(BaseModel):
    id: UUID
    event_type: str
    occurred_at: datetime
    run_id: UUID
    tool_id: UUID
    tool_name: str
    risk: str | None
    reason: str | None
    succeeded: bool | None
    invocation_id: UUID | None


def _service(session: AsyncSession, request: Request) -> ToolRegistryService:
    return ToolRegistryService(
        SqlAlchemyToolRepository(session),
        connection_probe=request.app.state.mcp_connection_probe,
        credential_resolver=request.app.state.tool_credential_resolver,
    )


def _reference_payload(references: list[object]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for item in references:
        if isinstance(item, ToolReference):
            payload.append(
                {
                    "tool_id": str(item.tool_id),
                    "employee_id": str(item.employee_id),
                    "employee_name": item.employee_name,
                    "relation": item.relation,
                    "version": item.version,
                }
            )
    return payload


def _raise_registry_error(error: Exception) -> None:
    if isinstance(error, (McpServerNotFound, ToolNotFound)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "resource_not_found", "message": "MCP Server 或 Tool 不存在"},
        ) from error
    if isinstance(error, ToolVersionNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tool_version_not_found", "message": "指定的工具版本不存在"},
        ) from error
    if isinstance(error, RegistryNameAlreadyExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "registry_name_exists", "message": "已存在同名注册项"},
        ) from error
    if isinstance(error, InvalidApprovalPolicy):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "approval_policy_invalid",
                "message": "破坏性风险等级不允许豁免审批",
            },
        ) from error
    if isinstance(error, InvalidToolSchema):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "tool_schema_invalid", "message": "工具输入 Schema 无效或超限"},
        ) from error
    if isinstance(error, ToolInUse):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "tool_in_use",
                "message": "工具仍被数字员工引用，无法删除",
                "references": _reference_payload(error.references),
            },
        ) from error
    if isinstance(error, McpServerInUse):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "mcp_server_in_use",
                "message": "MCP Server 下的工具仍被数字员工引用，无法删除",
                "references": _reference_payload(error.references),
            },
        ) from error
    if isinstance(error, McpConnectionFailed):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": error.code, "message": "MCP Server 连接失败"},
        ) from error
    if isinstance(error, LocalCredentialConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code, "message": "凭据配置无效或凭据服务不可用"},
        ) from error
    raise error


_REGISTRY_ERRORS = (
    McpServerNotFound,
    ToolNotFound,
    ToolVersionNotFound,
    RegistryNameAlreadyExists,
    InvalidApprovalPolicy,
    InvalidToolSchema,
    ToolInUse,
    McpServerInUse,
    McpConnectionFailed,
    LocalCredentialConfigurationError,
)


@mcp_router.post("", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    payload: McpServerCreate, request: Request, tenant_id: TenantHeader = None
) -> McpServerResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            server = await _service(session, request).register_server(
                tenant_id=access.tenant.id,
                created_by=user.id,
                name=payload.name,
                transport=payload.transport,
                endpoint=str(payload.endpoint) if payload.endpoint is not None else None,
                command=payload.command,
                args=payload.args,
                secret_reference=payload.secret_reference,
                enabled=payload.enabled,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.created",
                resource_type="mcp_server",
                resource_id=server.id,
                metadata={
                    "transport": server.transport.value,
                    "enabled": server.enabled,
                    "has_credentials": server.secret_reference is not None,
                },
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return McpServerResponse.from_entity(server)


@mcp_router.get("", response_model=list[McpServerResponse])
async def list_mcp_servers(
    request: Request, tenant_id: TenantHeader = None
) -> list[McpServerResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        servers = await _service(session, request).list_servers(tenant_id=access.tenant.id)
    return [McpServerResponse.from_entity(server) for server in servers]


@mcp_router.patch("/{server_id}", response_model=McpServerResponse)
async def update_mcp_server(
    server_id: UUID,
    payload: McpServerUpdate,
    request: Request,
    tenant_id: TenantHeader = None,
) -> McpServerResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            service = _service(session, request)
            current = await service.get_server(
                tenant_id=access.tenant.id, server_id=server_id
            )
            _validate_update_against_transport(current, payload)
            server = await service.update_server(
                tenant_id=access.tenant.id,
                server_id=server_id,
                name=payload.name,
                endpoint=(
                    str(payload.endpoint) if payload.endpoint is not None else None
                ),
                command=payload.command,
                args=payload.args,
                enabled=payload.enabled,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.updated",
                resource_type="mcp_server",
                resource_id=server.id,
                metadata={"enabled": server.enabled},
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return McpServerResponse.from_entity(server)


def _validate_update_against_transport(server: McpServer, payload: McpServerUpdate) -> None:
    if server.transport is McpTransport.STREAMABLE_HTTP and payload.command is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "transport_mismatch",
                "message": "streamable_http Server 不接受启动命令",
            },
        )
    if server.transport is McpTransport.STDIO and payload.endpoint is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "transport_mismatch",
                "message": "stdio Server 不接受服务地址",
            },
        )


@mcp_router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> None:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            service = _service(session, request)
            server = await service.get_server(
                tenant_id=access.tenant.id, server_id=server_id
            )
            await service.delete_server(
                tenant_id=access.tenant.id, server_id=server_id
            )
            if server.secret_reference is not None and server.secret_reference.startswith(
                MANAGED_SECRET_REFERENCE_PREFIX
            ):
                store = request.app.state.tool_credential_store
                await store.delete(
                    tenant_id=access.tenant.id, reference=server.secret_reference
                )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.deleted",
                resource_type="mcp_server",
                resource_id=server_id,
                metadata={},
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error


@mcp_router.post("/{server_id}/connection-test", response_model=ConnectionTestResponse)
async def test_mcp_server_connection(
    server_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> ConnectionTestResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            result = await _service(session, request).test_server_connection(
                tenant_id=access.tenant.id, server_id=server_id
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.connection_tested",
                resource_type="mcp_server",
                resource_id=server_id,
                metadata={
                    "status": result.status.value,
                    "error_code": result.error_code,
                },
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return ConnectionTestResponse.from_entity(result)


@mcp_router.post("/{server_id}/sync", response_model=SyncReportResponse)
async def sync_mcp_server(
    server_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> SyncReportResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        service = _service(session, request)
        try:
            report = await service.sync_server(
                tenant_id=access.tenant.id, server_id=server_id
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.synced",
                resource_type="mcp_server",
                resource_id=server_id,
                metadata={
                    "added": len(report.added),
                    "updated": len(report.updated),
                    "removed": len(report.removed),
                },
            )
            await session.commit()
        except McpConnectionFailed as error:
            # 失败报告与连接状态也要持久化，属于失败路径的可观测性。
            await session.commit()
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return SyncReportResponse.from_entity(report)


@mcp_router.get("/{server_id}/sync-reports", response_model=list[SyncReportResponse])
async def list_mcp_server_sync_reports(
    server_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SyncReportResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            reports = await _service(session, request).list_sync_reports(
                tenant_id=access.tenant.id, server_id=server_id, limit=limit
            )
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return [SyncReportResponse.from_entity(report) for report in reports]


@mcp_router.put("/{server_id}/credentials", response_model=McpServerResponse)
async def configure_mcp_server_credentials(
    server_id: UUID,
    payload: McpServerCredentialsUpdate,
    request: Request,
    tenant_id: TenantHeader = None,
) -> McpServerResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            service = _service(session, request)
            await service.get_server(tenant_id=access.tenant.id, server_id=server_id)
            reference = f"{MANAGED_SECRET_REFERENCE_PREFIX}{server_id}"
            store = request.app.state.tool_credential_store
            await store.store(
                tenant_id=access.tenant.id,
                reference=reference,
                values=payload.values,
            )
            server = await service.set_server_secret_reference(
                tenant_id=access.tenant.id,
                server_id=server_id,
                secret_reference=reference,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.credentials_configured",
                resource_type="mcp_server",
                resource_id=server_id,
                metadata={"credential_keys": ", ".join(sorted(payload.values.keys()))},
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return McpServerResponse.from_entity(server)


@mcp_router.delete("/{server_id}/credentials", response_model=McpServerResponse)
async def remove_mcp_server_credentials(
    server_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> McpServerResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            service = _service(session, request)
            current = await service.get_server(
                tenant_id=access.tenant.id, server_id=server_id
            )
            if current.secret_reference is not None and current.secret_reference.startswith(
                MANAGED_SECRET_REFERENCE_PREFIX
            ):
                store = request.app.state.tool_credential_store
                await store.delete(
                    tenant_id=access.tenant.id, reference=current.secret_reference
                )
            server = await service.set_server_secret_reference(
                tenant_id=access.tenant.id, server_id=server_id, secret_reference=None
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="mcp_server.credentials_removed",
                resource_type="mcp_server",
                resource_id=server_id,
                metadata={},
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return McpServerResponse.from_entity(server)


@tool_router.post("", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolCreate, request: Request, tenant_id: TenantHeader = None
) -> ToolResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            tool = await _service(session, request).register_tool(
                tenant_id=access.tenant.id,
                server_id=payload.server_id,
                name=payload.name,
                description=payload.description,
                input_schema=payload.input_schema,
                risk_level=payload.risk_level,
                approval_policy=payload.approval_policy,
                enabled=payload.enabled,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="tool.created",
                resource_type="tool",
                resource_id=tool.id,
                metadata={
                    "server_id": str(tool.server_id),
                    "risk_level": tool.risk_level.value,
                    "enabled": tool.enabled,
                },
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return ToolResponse.from_entity(tool)


@tool_router.get("", response_model=list[ToolResponse])
async def list_tools(
    request: Request,
    tenant_id: TenantHeader = None,
    server_id: Annotated[UUID | None, Query()] = None,
) -> list[ToolResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        tools = await _service(session, request).list_tools(
            tenant_id=access.tenant.id, server_id=server_id
        )
    return [ToolResponse.from_entity(tool) for tool in tools]


@tool_router.patch("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: UUID,
    payload: ToolUpdate,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ToolResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            tool = await _service(session, request).update_tool(
                tenant_id=access.tenant.id,
                tool_id=tool_id,
                description=payload.description,
                input_schema=payload.input_schema,
                risk_level=payload.risk_level,
                approval_policy=payload.approval_policy,
                enabled=payload.enabled,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="tool.updated",
                resource_type="tool",
                resource_id=tool.id,
                metadata={
                    "enabled": tool.enabled,
                    "risk_level": tool.risk_level.value,
                    "approval_policy": tool.approval_policy.value,
                    "version": tool.version,
                },
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return ToolResponse.from_entity(tool)


@tool_router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> None:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            await _service(session, request).delete_tool(
                tenant_id=access.tenant.id, tool_id=tool_id
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="tool.deleted",
                resource_type="tool",
                resource_id=tool_id,
                metadata={},
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error


@tool_router.get("/{tool_id}/versions", response_model=list[ToolVersionResponse])
async def list_tool_versions(
    tool_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> list[ToolVersionResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            versions = await _service(session, request).list_tool_versions(
                tenant_id=access.tenant.id, tool_id=tool_id
            )
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return [ToolVersionResponse.from_entity(item) for item in versions]


@tool_router.get("/{tool_id}/references", response_model=list[ToolReferenceResponse])
async def list_tool_references(
    tool_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> list[ToolReferenceResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            references = await _service(session, request).list_tool_references(
                tenant_id=access.tenant.id, tool_id=tool_id
            )
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return [ToolReferenceResponse.from_entity(item) for item in references]


@tool_router.post("/{tool_id}/rollback", response_model=ToolResponse)
async def rollback_tool(
    tool_id: UUID,
    payload: ToolRollbackRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ToolResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        try:
            tool = await _service(session, request).rollback_tool(
                tenant_id=access.tenant.id, tool_id=tool_id, version=payload.version
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="tool.rolled_back",
                resource_type="tool",
                resource_id=tool.id,
                metadata={
                    "restored_version": payload.version,
                    "new_version": tool.version,
                },
            )
            await session.commit()
        except _REGISTRY_ERRORS as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return ToolResponse.from_entity(tool)


@invocation_router.get("", response_model=list[ToolInvocationResponse])
async def list_tool_invocations(
    request: Request,
    tenant_id: TenantHeader = None,
    tool_id: Annotated[UUID | None, Query()] = None,
    server_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ToolInvocationResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.TOOLS_MANAGE,
        )
        tool_ids: list[UUID] | None = None
        if server_id is not None:
            tools = await _service(session, request).list_tools(
                tenant_id=access.tenant.id, server_id=server_id
            )
            tool_ids = [tool.id for tool in tools]
        records = await SqlAlchemyToolAuditReader(session).list_recent(
            tenant_id=access.tenant.id,
            tool_id=tool_id,
            tool_ids=tool_ids,
            limit=limit,
        )
    return [
        ToolInvocationResponse(
            id=record.id,
            event_type=record.event_type,
            occurred_at=record.occurred_at,
            run_id=record.run_id,
            tool_id=record.tool_id,
            tool_name=record.tool_name,
            risk=record.risk,
            reason=record.reason,
            succeeded=record.succeeded,
            invocation_id=record.invocation_id,
        )
        for record in records
    ]
