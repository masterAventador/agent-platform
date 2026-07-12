from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.tools import SqlAlchemyToolRepository
from agent_platform.platform.tools.entities import McpServer, McpTransport, Tool, ToolRiskLevel


@pytest.mark.asyncio
async def test_repository_resolves_tenant_scoped_gateway_definition() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    tenant_id = uuid4()
    server = McpServer.create(
        tenant_id=tenant_id,
        created_by=uuid4(),
        name="crm-mcp",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.internal.example/crm",
        command=None,
        args=[],
        secret_reference="vault://tenant/crm-mcp",
        enabled=True,
    )
    tool = Tool.create(
        tenant_id=tenant_id,
        server_id=server.id,
        name="crm.read",
        description="Read CRM data",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.READ,
        enabled=True,
    )

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.add_server(server)
        await repository.add_tool(tool)
        await session.commit()

        definition = await repository.resolve(tenant_id=tenant_id, tool_id=tool.id)
        hidden = await repository.resolve(tenant_id=uuid4(), tool_id=tool.id)

    assert definition is not None
    assert definition.tenant_id == tenant_id
    assert definition.tool_id == tool.id
    assert definition.server_id == server.id
    assert definition.name == tool.name
    assert definition.risk is ToolRiskLevel.READ
    assert definition.enabled is True
    assert definition.server_enabled is True
    assert definition.credential_references == ("vault://tenant/crm-mcp",)
    assert hidden is None
    await engine.dispose()
