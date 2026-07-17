"""SqlAlchemyWorkflowRepository 集成契约：真实 SQLAlchemy 会话下的增改查与唯一约束。"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.workflows import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowVersionRepository,
)
from agent_platform.platform.workflows.entities import Workflow, WorkflowVersion
from agent_platform.platform.workflows.errors import (
    WorkflowNameAlreadyExists,
    WorkflowVersionAlreadyExists,
)
from agent_platform.platform.workflows.services import WorkflowService


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as value:
        yield value
    await engine.dispose()


def _service(session: AsyncSession) -> WorkflowService:
    return WorkflowService(
        workflows=SqlAlchemyWorkflowRepository(session),
        versions=SqlAlchemyWorkflowVersionRepository(session),
    )


def _graph() -> dict[str, object]:
    return {
        "entrypoint": "a",
        "nodes": [{"name": "a", "type": "agent", "config": {"prompt": "hi"}, "next": None}],
    }


@pytest.mark.asyncio
async def test_register_publish_rollback_roundtrip(session: AsyncSession) -> None:
    service = _service(session)
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="流程A", description="d", graph=_graph()
    )
    await service.add_version(
        tenant_id=tenant_id,
        workflow_id=workflow.id,
        created_by=user_id,
        graph=_graph(),
        description="v2",
    )
    await service.publish(
        tenant_id=tenant_id, workflow_id=workflow.id, version=2, published_by=user_id
    )
    await session.commit()

    fetched = await service.get(tenant_id=tenant_id, workflow_id=workflow.id)
    assert fetched.published_version == 2
    assert fetched.latest_version == 2

    rolled = await service.rollback(
        tenant_id=tenant_id, workflow_id=workflow.id, version=1, rolled_back_by=user_id
    )
    await session.commit()
    assert rolled.published_version == 1

    versions = await service.list_versions(tenant_id=tenant_id, workflow_id=workflow.id)
    assert [v.version for v in versions] == [2, 1]
    reference = await service.published_reference(tenant_id=tenant_id, workflow_id=workflow.id)
    assert reference == 1


@pytest.mark.asyncio
async def test_duplicate_name_rejected_case_insensitive(session: AsyncSession) -> None:
    service = _service(session)
    tenant_id, user_id = uuid4(), uuid4()
    await service.register(
        tenant_id=tenant_id, created_by=user_id, name="Dup", description="", graph=_graph()
    )
    await session.commit()
    with pytest.raises(WorkflowNameAlreadyExists):
        await service.register(
            tenant_id=tenant_id, created_by=user_id, name="dup", description="", graph=_graph()
        )


@pytest.mark.asyncio
async def test_duplicate_version_number_is_controlled_conflict(session: AsyncSession) -> None:
    """并发/重复 add 同一 (workflow_id, version) 撞唯一约束 → 受控冲突错误，session 不破损。"""

    service = _service(session)
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="并发流程", description="", graph=_graph()
    )
    await session.commit()

    versions = SqlAlchemyWorkflowVersionRepository(session)
    duplicate = WorkflowVersion.create(
        workflow=Workflow(
            id=workflow.id,
            tenant_id=tenant_id,
            name=workflow.name,
            description="",
            latest_version=1,
            published_version=None,
            status=workflow.status,
            created_by=user_id,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
        ),
        version=1,  # 已存在的版本号
        graph=_graph(),
        description="dup",
        created_by=user_id,
    )
    with pytest.raises(WorkflowVersionAlreadyExists):
        await versions.add(duplicate)


@pytest.mark.asyncio
async def test_tenant_isolation(session: AsyncSession) -> None:
    service = _service(session)
    tenant_a, tenant_b, user_id = uuid4(), uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_a, created_by=user_id, name="共享名", description="", graph=_graph()
    )
    await session.commit()
    # 另一个租户可用同名，且看不到 tenant_a 的工作流。
    await service.register(
        tenant_id=tenant_b, created_by=user_id, name="共享名", description="", graph=_graph()
    )
    await session.commit()
    listed_b = await service.list_all(tenant_id=tenant_b)
    assert all(w.id != workflow.id for w in listed_b)
    from agent_platform.platform.workflows.errors import WorkflowNotFound

    with pytest.raises(WorkflowNotFound):
        await service.get(tenant_id=tenant_b, workflow_id=workflow.id)
