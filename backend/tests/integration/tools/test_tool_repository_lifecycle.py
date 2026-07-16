"""C09 SqlAlchemyToolRepository 生命周期能力集成测试（sqlite）。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.platform.tools.entities import (
    McpConnectionStatus,
    McpServer,
    McpSyncRemovedEntry,
    McpSyncReport,
    McpTransport,
    Tool,
    ToolApprovalPolicy,
    ToolOrigin,
    ToolRiskLevel,
)


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _server(tenant_id) -> McpServer:
    return McpServer.create(
        tenant_id=tenant_id,
        created_by=uuid4(),
        name="lifecycle-mcp",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://mcp.internal.example/crm",
        command=None,
        args=[],
        secret_reference=None,
        enabled=True,
    )


def _tool(tenant_id, server_id) -> Tool:
    return Tool.create(
        tenant_id=tenant_id,
        server_id=server_id,
        name="crm.read",
        description="Read CRM data",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.READ,
        enabled=True,
        origin=ToolOrigin.DISCOVERED,
        approval_policy=ToolApprovalPolicy.ALWAYS,
    )


@pytest.mark.asyncio
async def test_round_trips_lifecycle_fields_versions_and_reports() -> None:
    session_factory = await _session_factory()
    tenant_id = uuid4()
    server = _server(tenant_id)
    tool = _tool(tenant_id, server.id)

    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.add_server(server)
        await repository.add_tool(tool)
        await repository.add_tool_version(tool.snapshot(change_source="initial"))
        await session.commit()

    now = datetime.now(UTC)
    updated_tool = tool.with_definition(
        description="v2", risk_level=ToolRiskLevel.WRITE
    ).mark_upstream_missing(missing=True, at=now)
    updated_server = server.with_connection_result(
        status=McpConnectionStatus.FAILED, error_code="mcp_timeout", tested_at=now
    ).with_synced_at(now)

    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.update_tool(updated_tool)
        await repository.add_tool_version(updated_tool.snapshot(change_source="update"))
        await repository.update_server(updated_server)
        await repository.add_sync_report(
            McpSyncReport(
                id=uuid4(),
                tenant_id=tenant_id,
                server_id=server.id,
                occurred_at=now,
                status="ok",
                added=["a"],
                updated=["b"],
                removed=[McpSyncRemovedEntry(name="c", referenced=True)],
                unchanged=3,
            ),
            keep=20,
        )
        await session.commit()

    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        stored_tool = await repository.get_tool(tenant_id=tenant_id, tool_id=tool.id)
        stored_server = await repository.get_server(
            tenant_id=tenant_id, server_id=server.id
        )
        locked_server = await repository.get_server_for_update(
            tenant_id=tenant_id, server_id=server.id
        )
        versions = await repository.list_tool_versions(
            tenant_id=tenant_id, tool_id=tool.id
        )
        snapshot = await repository.get_tool_version(
            tenant_id=tenant_id, tool_id=tool.id, version=1
        )
        reports = await repository.list_sync_reports(
            tenant_id=tenant_id, server_id=server.id, limit=10
        )
        definition = await repository.resolve(tenant_id=tenant_id, tool_id=tool.id)

    assert stored_tool is not None
    assert stored_tool.version == 2
    assert stored_tool.upstream_missing is True
    assert stored_tool.origin is ToolOrigin.DISCOVERED
    assert stored_tool.approval_policy is ToolApprovalPolicy.ALWAYS
    assert stored_server is not None
    assert stored_server.connection_status is McpConnectionStatus.FAILED
    assert stored_server.connection_error_code == "mcp_timeout"
    assert stored_server.last_synced_at is not None
    assert locked_server is not None and locked_server.id == server.id
    assert [item.version for item in versions] == [1, 2]
    assert snapshot is not None and snapshot.risk_level is ToolRiskLevel.READ
    assert len(reports) == 1
    assert reports[0].removed[0].referenced is True
    assert definition is not None
    assert definition.approval_policy is ToolApprovalPolicy.ALWAYS
    assert definition.upstream_missing is True


@pytest.mark.asyncio
async def test_sync_report_pruning_keeps_recent_reports_only() -> None:
    session_factory = await _session_factory()
    tenant_id = uuid4()
    server = _server(tenant_id)
    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.add_server(server)
        for index in range(25):
            await repository.add_sync_report(
                McpSyncReport(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    server_id=server.id,
                    occurred_at=datetime(2026, 7, 16, 0, index, tzinfo=UTC),
                    status="ok",
                ),
                keep=20,
            )
        await session.commit()
        reports = await repository.list_sync_reports(
            tenant_id=tenant_id, server_id=server.id, limit=100
        )
    assert len(reports) == 20
    assert reports[0].occurred_at.minute == 24


@pytest.mark.asyncio
async def test_references_scan_drafts_and_published_versions() -> None:
    session_factory = await _session_factory()
    tenant_id = uuid4()
    server = _server(tenant_id)
    tool = _tool(tenant_id, server.id)
    employee_id = uuid4()

    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.add_server(server)
        await repository.add_tool(tool)
        session.add(
            EmployeeRecord(
                id=employee_id,
                tenant_id=tenant_id,
                created_by=uuid4(),
                name="研究员",
                avatar_url=None,
                role_description="",
                visibility="tenant",
                runtime_type="autonomous",
                system_prompt="",
                model_settings={},
                input_schema={},
                output_schema={},
                capabilities={},
                skill_ids=[],
                tool_ids=[str(tool.id)],
                knowledge_base_ids=[],
                approval_policy={},
                release_strategy={},
                status="published",
                published_version=3,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.add(
            EmployeeVersionRecord(
                id=uuid4(),
                employee_id=employee_id,
                tenant_id=tenant_id,
                version=3,
                definition={"name": "研究员", "tool_ids": [str(tool.id)]},
                published_by=uuid4(),
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()

        references = await repository.list_tool_references(
            tenant_id=tenant_id, tool_ids=[tool.id]
        )
        foreign = await repository.list_tool_references(
            tenant_id=uuid4(), tool_ids=[tool.id]
        )

    relations = sorted(item.relation for item in references)
    assert relations == ["employee_draft", "employee_version"]
    assert foreign == []


@pytest.mark.asyncio
async def test_delete_server_cascades_tools_versions_and_reports() -> None:
    session_factory = await _session_factory()
    tenant_id = uuid4()
    server = _server(tenant_id)
    tool = _tool(tenant_id, server.id)
    async with session_factory() as session:
        repository = SqlAlchemyToolRepository(session)
        await repository.add_server(server)
        await repository.add_tool(tool)
        await repository.add_tool_version(tool.snapshot(change_source="initial"))
        await repository.add_sync_report(
            McpSyncReport(
                id=uuid4(),
                tenant_id=tenant_id,
                server_id=server.id,
                occurred_at=datetime.now(UTC),
                status="ok",
            ),
            keep=20,
        )
        await session.commit()

        await repository.delete_server(tenant_id=tenant_id, server_id=server.id)
        await session.commit()

        assert await repository.get_server(tenant_id=tenant_id, server_id=server.id) is None
        assert await repository.list_tools(tenant_id=tenant_id) == []
        assert (
            await repository.list_tool_versions(tenant_id=tenant_id, tool_id=tool.id)
            == []
        )
        assert (
            await repository.list_sync_reports(
                tenant_id=tenant_id, server_id=server.id, limit=10
            )
            == []
        )
