from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tools import SqlAlchemyToolRepository
from agent_platform.infrastructure.mcp import (
    DatabaseMCPClientResolver,
    MCPClient,
    MCPServerConfig,
    MCPServerConfigurationError,
    MCPServerUnavailableError,
    MCPStdioConfig,
    MCPStdioExecutionDeniedError,
    MCPStreamableHTTPConfig,
    StdioExecutionPolicy,
)
from agent_platform.platform.tools.entities import McpServer, McpTransport


class FakeClient:
    async def list_tools(self) -> list[object]:
        return []

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> JsonValue:
        return {"name": name, "argument_count": len(arguments)}


@dataclass
class RecordingClientBuilder:
    configs: list[MCPServerConfig] = field(default_factory=list)
    client: FakeClient = field(default_factory=FakeClient)

    def __call__(self, config: MCPServerConfig) -> MCPClient:
        self.configs.append(config)
        return self.client


class LeakingClientBuilder:
    def __call__(self, config: MCPServerConfig) -> MCPClient:
        assert isinstance(config, MCPStreamableHTTPConfig)
        raise RuntimeError(f"failed for {config.url}: {config.headers['Authorization']}")


@dataclass
class AllowStdioPolicy:
    requests: list[tuple[UUID, UUID, str, tuple[str, ...]]] = field(default_factory=list)

    async def allows(
        self,
        *,
        tenant_id: UUID,
        server_id: UUID,
        command: str,
        args: tuple[str, ...],
    ) -> bool:
        self.requests.append((tenant_id, server_id, command, args))
        return True


def make_server(
    *,
    tenant_id: UUID,
    transport: McpTransport,
    enabled: bool = True,
    endpoint: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
) -> McpServer:
    return McpServer.create(
        tenant_id=tenant_id,
        created_by=uuid4(),
        name=f"server-{uuid4()}",
        transport=transport,
        endpoint=endpoint,
        command=command,
        args=args or [],
        secret_reference="vault://tenant/server",
        enabled=enabled,
    )


async def create_repository() -> tuple[AsyncEngine, AsyncSession, SqlAlchemyToolRepository]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    return (engine, session, SqlAlchemyToolRepository(session))


@pytest.mark.asyncio
async def test_http_server_is_tenant_scoped_and_credentials_become_headers() -> None:
    engine, session, repository = await create_repository()
    tenant_id = uuid4()
    server = make_server(
        tenant_id=tenant_id,
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.internal.example/rpc",
    )
    await repository.add_server(server)
    builder = RecordingClientBuilder()
    resolver = DatabaseMCPClientResolver(repository, client_builder=builder)
    credentials = {"Authorization": "Bearer do-not-log", "X-Tenant-Key": "secret"}

    client = await resolver.resolve(
        tenant_id=tenant_id,
        server_id=server.id,
        credentials=credentials,
    )

    assert client is builder.client
    assert len(builder.configs) == 1
    config = builder.configs[0]
    assert isinstance(config, MCPStreamableHTTPConfig)
    assert config.url == server.endpoint
    assert config.headers == credentials
    assert "do-not-log" not in repr(config)
    assert "secret" not in repr(resolver)

    with pytest.raises(MCPServerUnavailableError) as captured:
        await resolver.resolve(
            tenant_id=uuid4(),
            server_id=server.id,
            credentials=credentials,
        )
    assert captured.value.code == "mcp_server_unavailable"
    assert str(captured.value) == "MCP server is unavailable"
    assert "do-not-log" not in repr(captured.value)
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_server_uses_same_stable_unavailable_failure() -> None:
    engine, session, repository = await create_repository()
    tenant_id = uuid4()
    server = make_server(
        tenant_id=tenant_id,
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.internal.example/rpc?private=config",
        enabled=False,
    )
    await repository.add_server(server)
    builder = RecordingClientBuilder()
    resolver = DatabaseMCPClientResolver(repository, client_builder=builder)

    with pytest.raises(MCPServerUnavailableError) as captured:
        await resolver.resolve(
            tenant_id=tenant_id,
            server_id=server.id,
            credentials={"Authorization": "Bearer do-not-log"},
        )

    assert captured.value.code == "mcp_server_unavailable"
    assert str(captured.value) == "MCP server is unavailable"
    assert "private" not in repr(captured.value)
    assert builder.configs == []
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_client_builder_failure_does_not_expose_credentials_or_server_config() -> None:
    engine, session, repository = await create_repository()
    tenant_id = uuid4()
    server = make_server(
        tenant_id=tenant_id,
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://private.internal.example/rpc",
    )
    await repository.add_server(server)
    resolver = DatabaseMCPClientResolver(repository, client_builder=LeakingClientBuilder())

    with pytest.raises(MCPServerConfigurationError) as captured:
        await resolver.resolve(
            tenant_id=tenant_id,
            server_id=server.id,
            credentials={"Authorization": "Bearer do-not-log"},
        )

    assert captured.value.code == "mcp_server_invalid_config"
    assert str(captured.value) == "MCP server configuration is invalid"
    rendered = repr(captured.value)
    assert "do-not-log" not in rendered
    assert "private.internal" not in rendered
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@mcp.internal.example/rpc",
        "https://mcp.internal.example/rpc?token=private",
        "https://mcp.internal.example/rpc#private",
    ],
)
async def test_http_endpoint_rejects_embedded_sensitive_components(endpoint: str) -> None:
    engine, session, repository = await create_repository()
    tenant_id = uuid4()
    server = make_server(
        tenant_id=tenant_id,
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint=endpoint,
    )
    await repository.add_server(server)
    builder = RecordingClientBuilder()
    resolver = DatabaseMCPClientResolver(repository, client_builder=builder)

    with pytest.raises(MCPServerConfigurationError) as captured:
        await resolver.resolve(
            tenant_id=tenant_id,
            server_id=server.id,
            credentials={"Authorization": "Bearer do-not-log"},
        )

    assert captured.value.code == "mcp_server_invalid_config"
    assert str(captured.value) == "MCP server configuration is invalid"
    assert "private" not in repr(captured.value)
    assert "password" not in repr(captured.value)
    assert builder.configs == []
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_stdio_is_denied_by_default_without_building_a_client() -> None:
    engine, session, repository = await create_repository()
    tenant_id = uuid4()
    server = make_server(
        tenant_id=tenant_id,
        transport=McpTransport.STDIO,
        command="/usr/bin/python3",
        args=["server.py", "--token=do-not-log"],
    )
    await repository.add_server(server)
    builder = RecordingClientBuilder()
    resolver = DatabaseMCPClientResolver(repository, client_builder=builder)

    with pytest.raises(MCPStdioExecutionDeniedError) as captured:
        await resolver.resolve(
            tenant_id=tenant_id,
            server_id=server.id,
            credentials={"API_TOKEN": "do-not-log"},
        )

    assert captured.value.code == "mcp_stdio_execution_denied"
    assert str(captured.value) == "MCP stdio execution is denied"
    assert "do-not-log" not in repr(captured.value)
    assert builder.configs == []
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_allowed_stdio_keeps_command_args_separate_and_credentials_in_env() -> None:
    engine, session, repository = await create_repository()
    tenant_id = uuid4()
    server = make_server(
        tenant_id=tenant_id,
        transport=McpTransport.STDIO,
        command="/usr/bin/python3",
        args=["server.py", "; touch /tmp/must-not-run"],
    )
    await repository.add_server(server)
    builder = RecordingClientBuilder()
    policy = AllowStdioPolicy()
    assert isinstance(policy, StdioExecutionPolicy)
    resolver = DatabaseMCPClientResolver(
        repository,
        client_builder=builder,
        stdio_policy=policy,
    )
    credentials = {"API_TOKEN": "do-not-log"}

    client = await resolver.resolve(
        tenant_id=tenant_id,
        server_id=server.id,
        credentials=credentials,
    )

    assert client is builder.client
    assert policy.requests == [
        (tenant_id, server.id, "/usr/bin/python3", ("server.py", "; touch /tmp/must-not-run"))
    ]
    config = builder.configs[0]
    assert isinstance(config, MCPStdioConfig)
    assert config.command == "/usr/bin/python3"
    assert config.args == ("server.py", "; touch /tmp/must-not-run")
    assert config.env == credentials
    assert "do-not-log" not in repr(config)
    await session.close()
    await engine.dispose()
