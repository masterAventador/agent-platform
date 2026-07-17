"""WorkflowService 单元测试：注册、加版本、发布、回滚、稳定引用与图校验。"""

from uuid import UUID, uuid4

import pytest

from agent_platform.platform.workflows.entities import (
    Workflow,
    WorkflowStatus,
    WorkflowVersion,
)
from agent_platform.platform.workflows.errors import (
    WorkflowNameAlreadyExists,
    WorkflowNotFound,
    WorkflowNotPublished,
    WorkflowVersionNotFound,
)
from agent_platform.platform.workflows.graph_spec import InvalidWorkflowGraph
from agent_platform.platform.workflows.ports import (
    WorkflowRepository,
    WorkflowVersionRepository,
)
from agent_platform.platform.workflows.services import WorkflowService


class FakeWorkflowRepository(WorkflowRepository):
    def __init__(self) -> None:
        self.workflows: dict[tuple[UUID, UUID], Workflow] = {}

    async def add(self, workflow: Workflow) -> None:
        for existing in self.workflows.values():
            if existing.tenant_id == workflow.tenant_id and (
                existing.name.lower() == workflow.name.lower()
            ):
                raise WorkflowNameAlreadyExists
        self.workflows[(workflow.tenant_id, workflow.id)] = workflow

    async def update(self, workflow: Workflow) -> None:
        self.workflows[(workflow.tenant_id, workflow.id)] = workflow

    async def get(self, *, tenant_id: UUID, workflow_id: UUID) -> Workflow | None:
        return self.workflows.get((tenant_id, workflow_id))

    async def list(self, *, tenant_id: UUID) -> list[Workflow]:
        return [w for (t, _), w in self.workflows.items() if t == tenant_id]


class FakeWorkflowVersionRepository(WorkflowVersionRepository):
    def __init__(self) -> None:
        self.versions: list[WorkflowVersion] = []

    async def add(self, version: WorkflowVersion) -> None:
        self.versions.append(version)

    async def get(
        self, *, tenant_id: UUID, workflow_id: UUID, version: int
    ) -> WorkflowVersion | None:
        for candidate in self.versions:
            if (
                candidate.tenant_id == tenant_id
                and candidate.workflow_id == workflow_id
                and candidate.version == version
            ):
                return candidate
        return None

    async def list(self, *, tenant_id: UUID, workflow_id: UUID) -> list[WorkflowVersion]:
        return sorted(
            (
                v
                for v in self.versions
                if v.tenant_id == tenant_id and v.workflow_id == workflow_id
            ),
            key=lambda v: v.version,
            reverse=True,
        )


def _service() -> tuple[WorkflowService, FakeWorkflowRepository, FakeWorkflowVersionRepository]:
    workflows = FakeWorkflowRepository()
    versions = FakeWorkflowVersionRepository()
    return WorkflowService(workflows=workflows, versions=versions), workflows, versions


def _graph() -> dict[str, object]:
    return {
        "entrypoint": "a",
        "nodes": [{"name": "a", "type": "agent", "config": {"prompt": "hi"}, "next": None}],
    }


@pytest.mark.asyncio
async def test_register_creates_draft_v1() -> None:
    service, _, versions = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id,
        created_by=user_id,
        name="客服流程",
        description="标准客服",
        graph=_graph(),
    )
    assert workflow.status is WorkflowStatus.DRAFT
    assert workflow.latest_version == 1
    assert workflow.published_version is None
    stored = await versions.get(tenant_id=tenant_id, workflow_id=workflow.id, version=1)
    assert stored is not None


@pytest.mark.asyncio
async def test_register_rejects_invalid_graph() -> None:
    service, _, _ = _service()
    with pytest.raises(InvalidWorkflowGraph):
        await service.register(
            tenant_id=uuid4(),
            created_by=uuid4(),
            name="坏流程",
            description="",
            graph={"entrypoint": "ghost", "nodes": [{"name": "a", "type": "agent", "next": None}]},
        )


@pytest.mark.asyncio
async def test_duplicate_name_rejected() -> None:
    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    await service.register(
        tenant_id=tenant_id, created_by=user_id, name="dup", description="", graph=_graph()
    )
    with pytest.raises(WorkflowNameAlreadyExists):
        await service.register(
            tenant_id=tenant_id, created_by=user_id, name="DUP", description="", graph=_graph()
        )


@pytest.mark.asyncio
async def test_add_version_increments_latest() -> None:
    service, _, versions = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
    )
    updated = await service.add_version(
        tenant_id=tenant_id,
        workflow_id=workflow.id,
        created_by=user_id,
        graph=_graph(),
        description="v2",
    )
    assert updated.latest_version == 2
    assert await versions.get(tenant_id=tenant_id, workflow_id=workflow.id, version=2) is not None


@pytest.mark.asyncio
async def test_publish_sets_published_version() -> None:
    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
    )
    published = await service.publish(
        tenant_id=tenant_id, workflow_id=workflow.id, version=1, published_by=user_id
    )
    assert published.status is WorkflowStatus.PUBLISHED
    assert published.published_version == 1


@pytest.mark.asyncio
async def test_publish_unknown_version_rejected() -> None:
    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
    )
    with pytest.raises(WorkflowVersionNotFound):
        await service.publish(
            tenant_id=tenant_id, workflow_id=workflow.id, version=5, published_by=user_id
        )


@pytest.mark.asyncio
async def test_rollback_to_earlier_version() -> None:
    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
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
    rolled = await service.rollback(
        tenant_id=tenant_id, workflow_id=workflow.id, version=1, rolled_back_by=user_id
    )
    assert rolled.published_version == 1
    assert rolled.latest_version == 2  # rollback 不改变版本历史


@pytest.mark.asyncio
async def test_rollback_requires_existing_version() -> None:
    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
    )
    await service.publish(
        tenant_id=tenant_id, workflow_id=workflow.id, version=1, published_by=user_id
    )
    with pytest.raises(WorkflowVersionNotFound):
        await service.rollback(
            tenant_id=tenant_id, workflow_id=workflow.id, version=9, rolled_back_by=user_id
        )


@pytest.mark.asyncio
async def test_operations_require_existing_workflow() -> None:
    service, _, _ = _service()
    with pytest.raises(WorkflowNotFound):
        await service.publish(
            tenant_id=uuid4(), workflow_id=uuid4(), version=1, published_by=uuid4()
        )


@pytest.mark.asyncio
async def test_published_reference_is_stable_after_new_version() -> None:
    """员工固化引用后，工作流继续加版本/回滚不改变已固化版本的图内容。"""

    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
    )
    await service.publish(
        tenant_id=tenant_id, workflow_id=workflow.id, version=1, published_by=user_id
    )
    fixated = await service.published_reference(tenant_id=tenant_id, workflow_id=workflow.id)
    assert fixated == 1
    # 新增并发布 v2 后，v1 的图快照仍可读取且不变。
    new_graph = {
        "entrypoint": "x",
        "nodes": [{"name": "x", "type": "tool", "config": {"tool": "t"}, "next": None}],
    }
    await service.add_version(
        tenant_id=tenant_id,
        workflow_id=workflow.id,
        created_by=user_id,
        graph=new_graph,
        description="v2",
    )
    v1 = await service.get_version(tenant_id=tenant_id, workflow_id=workflow.id, version=1)
    v2 = await service.get_version(tenant_id=tenant_id, workflow_id=workflow.id, version=2)
    assert v1.graph["entrypoint"] == "a"
    assert v1.graph["nodes"][0]["name"] == "a"  # type: ignore[index]
    assert v2.graph["entrypoint"] == "x"
    assert v1.graph != v2.graph  # 固化版本互不影响


@pytest.mark.asyncio
async def test_published_reference_requires_published() -> None:
    service, _, _ = _service()
    tenant_id, user_id = uuid4(), uuid4()
    workflow = await service.register(
        tenant_id=tenant_id, created_by=user_id, name="wf", description="", graph=_graph()
    )
    with pytest.raises(WorkflowNotPublished):
        await service.published_reference(tenant_id=tenant_id, workflow_id=workflow.id)
