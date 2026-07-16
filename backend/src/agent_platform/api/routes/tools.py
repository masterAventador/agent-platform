from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.tools import SqlAlchemyToolRepository
from agent_platform.platform.tenants.permissions import TenantPermission
from agent_platform.platform.tools.entities import (
    McpServer,
    McpTransport,
    Tool,
    ToolRiskLevel,
)
from agent_platform.platform.tools.errors import (
    McpServerNotFound,
    RegistryNameAlreadyExists,
    ToolNotFound,
)
from agent_platform.platform.tools.services import ToolRegistryService

TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
CommandArgument = Annotated[str, Field(max_length=500)]
mcp_router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers"])
tool_router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


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
            if self.endpoint.username is not None or self.endpoint.password is not None:
                raise ValueError("endpoint must not contain credentials")
            if self.endpoint.query is not None or self.endpoint.fragment is not None:
                raise ValueError("endpoint must not contain query parameters or fragments")
        elif self.command is None or self.endpoint is not None:
            raise ValueError("stdio transport requires command and does not accept endpoint")
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
        )


class McpServerEnabledUpdate(BaseModel):
    enabled: bool


class ToolCreate(BaseModel):
    server_id: UUID
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel = ToolRiskLevel.READ
    enabled: bool = True


class ToolEnabledUpdate(BaseModel):
    enabled: bool


class ToolResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    server_id: UUID
    name: str
    description: str
    input_schema: dict[str, object]
    risk_level: ToolRiskLevel
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
            enabled=tool.enabled,
        )


def _service(session: AsyncSession) -> ToolRegistryService:
    return ToolRegistryService(SqlAlchemyToolRepository(session))


def _raise_registry_error(error: Exception) -> None:
    if isinstance(error, (McpServerNotFound, ToolNotFound)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "resource_not_found", "message": "MCP Server 或 Tool 不存在"},
        ) from error
    if isinstance(error, RegistryNameAlreadyExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "registry_name_exists", "message": "已存在同名注册项"},
        ) from error
    raise error


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
            server = await _service(session).register_server(
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
        except RegistryNameAlreadyExists as error:
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
        servers = await _service(session).list_servers(tenant_id=access.tenant.id)
    return [McpServerResponse.from_entity(server) for server in servers]


@mcp_router.patch("/{server_id}", response_model=McpServerResponse)
async def set_mcp_server_enabled(
    server_id: UUID,
    payload: McpServerEnabledUpdate,
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
            server = await _service(session).set_server_enabled(
                tenant_id=access.tenant.id, server_id=server_id, enabled=payload.enabled
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
        except McpServerNotFound as error:
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
            tool = await _service(session).register_tool(
                tenant_id=access.tenant.id,
                server_id=payload.server_id,
                name=payload.name,
                description=payload.description,
                input_schema=payload.input_schema,
                risk_level=payload.risk_level,
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
        except (McpServerNotFound, RegistryNameAlreadyExists) as error:
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
        tools = await _service(session).list_tools(
            tenant_id=access.tenant.id, server_id=server_id
        )
    return [ToolResponse.from_entity(tool) for tool in tools]


@tool_router.patch("/{tool_id}", response_model=ToolResponse)
async def set_tool_enabled(
    tool_id: UUID,
    payload: ToolEnabledUpdate,
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
            tool = await _service(session).set_tool_enabled(
                tenant_id=access.tenant.id, tool_id=tool_id, enabled=payload.enabled
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="tool.updated",
                resource_type="tool",
                resource_id=tool.id,
                metadata={"enabled": tool.enabled},
            )
            await session.commit()
        except ToolNotFound as error:
            _raise_registry_error(error)
            raise AssertionError("unreachable") from error
    return ToolResponse.from_entity(tool)
