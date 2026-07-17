"""员工与工作流绑定：编辑器只能引用已注册流程，发布时固化已发布版本、回滚不改旧版本语义。"""

from uuid import UUID, uuid4

import pytest

from agent_platform.platform.employees.entities import (
    EmployeeDraft,
    EmployeeVisibility,
    RuntimeType,
    is_runnable_employee_definition,
)
from agent_platform.platform.employees.errors import (
    EmployeeConfigurationUnavailable,
    EmployeeWorkflowNotBindable,
)
from agent_platform.platform.employees.services import EmployeeService


class _FakeEmployeeRepo:
    def __init__(self) -> None:
        self.store: dict[UUID, object] = {}

    async def add(self, employee: object) -> None:
        self.store[employee.id] = employee  # type: ignore[attr-defined]

    async def update(self, employee: object) -> None:
        self.store[employee.id] = employee  # type: ignore[attr-defined]

    async def get(self, *, tenant_id: UUID, employee_id: UUID) -> object | None:
        employee = self.store.get(employee_id)
        if employee is None or employee.tenant_id != tenant_id:  # type: ignore[attr-defined]
            return None
        return employee

    async def list(self, *, tenant_id: UUID) -> list[object]:
        return [e for e in self.store.values() if e.tenant_id == tenant_id]  # type: ignore[attr-defined]


class _FakeVersionRepo:
    def __init__(self) -> None:
        self.versions: list[object] = []

    async def add(self, version: object) -> None:
        self.versions.append(version)

    async def list(self, *, tenant_id: UUID, employee_id: UUID) -> list[object]:
        return [
            v
            for v in self.versions
            if v.tenant_id == tenant_id and v.employee_id == employee_id  # type: ignore[attr-defined]
        ]


class _AllowPolicy:
    async def are_bindable(self, **kwargs: object) -> bool:
        return True

    async def published_versions(self, **kwargs: object) -> dict[UUID, int]:
        return {}


class _FakeWorkflowPolicy:
    def __init__(
        self,
        *,
        registered: set[UUID] | None = None,
        published: dict[UUID, int] | None = None,
    ) -> None:
        self._registered = registered or set()
        self._published = published or {}

    async def is_registered(self, *, tenant_id: UUID, workflow_id: UUID) -> bool:
        return workflow_id in self._registered

    async def published_version(self, *, tenant_id: UUID, workflow_id: UUID) -> int | None:
        return self._published.get(workflow_id)

    def set_published(self, workflow_id: UUID, version: int | None) -> None:
        if version is None:
            self._published.pop(workflow_id, None)
        else:
            self._published[workflow_id] = version


def _service(workflow_policy: _FakeWorkflowPolicy) -> tuple[EmployeeService, _FakeVersionRepo]:
    versions = _FakeVersionRepo()
    service = EmployeeService(
        employees=_FakeEmployeeRepo(),
        versions=versions,
        skills=_AllowPolicy(),
        tools=_AllowPolicy(),
        knowledge_bases=_AllowPolicy(),
        workflows=workflow_policy,
    )
    return service, versions


def _draft(runtime_type: RuntimeType, workflow_id: UUID | None) -> EmployeeDraft:
    return EmployeeDraft(
        name="流程员工",
        avatar_url=None,
        role_description="处理固定流程",
        visibility=EmployeeVisibility.TENANT,
        runtime_type=runtime_type,
        system_prompt="按流程执行",
        model_settings={"alias": "default"},
        input_schema={},
        output_schema={},
        capabilities={"conversation": True, "scheduled_tasks": False, "file_upload": False},
        skill_ids=[],
        tool_ids=[],
        knowledge_base_ids=[],
        approval_policy={},
        release_strategy={"mode": "all"},
        workflow_id=workflow_id,
    )


def test_is_runnable_workflow_requires_fixated_reference() -> None:
    draft = _draft(RuntimeType.WORKFLOW, uuid4())
    # 草稿快照未固化版本时不可运行。
    assert not is_runnable_employee_definition(draft.snapshot())
    # 固化了 workflow_version 后可运行。
    assert is_runnable_employee_definition(draft.snapshot(workflow_version=3))


def test_is_runnable_hybrid_requires_reference() -> None:
    draft = _draft(RuntimeType.HYBRID, uuid4())
    assert is_runnable_employee_definition(draft.snapshot(workflow_version=1))
    no_ref = _draft(RuntimeType.HYBRID, None)
    assert not is_runnable_employee_definition(no_ref.snapshot(workflow_version=1))


def test_autonomous_snapshot_has_null_workflow_reference() -> None:
    draft = _draft(RuntimeType.AUTONOMOUS, None)
    snapshot = draft.snapshot()
    assert snapshot["workflow_id"] is None
    assert is_runnable_employee_definition(snapshot)


@pytest.mark.asyncio
async def test_create_rejects_unregistered_workflow() -> None:
    workflow_id = uuid4()
    service, _ = _service(_FakeWorkflowPolicy(registered=set()))
    with pytest.raises(EmployeeWorkflowNotBindable):
        await service.create(
            tenant_id=uuid4(),
            created_by=uuid4(),
            draft=_draft(RuntimeType.WORKFLOW, workflow_id),
        )


@pytest.mark.asyncio
async def test_publish_requires_published_workflow() -> None:
    tenant_id, user_id, workflow_id = uuid4(), uuid4(), uuid4()
    policy = _FakeWorkflowPolicy(registered={workflow_id}, published={})
    service, _ = _service(policy)
    employee = await service.create(
        tenant_id=tenant_id,
        created_by=user_id,
        draft=_draft(RuntimeType.WORKFLOW, workflow_id),
    )
    with pytest.raises(EmployeeConfigurationUnavailable):
        await service.publish(
            tenant_id=tenant_id, employee_id=employee.id, published_by=user_id
        )


@pytest.mark.asyncio
async def test_publish_fixates_workflow_version_and_rollback_does_not_change_it() -> None:
    tenant_id, user_id, workflow_id = uuid4(), uuid4(), uuid4()
    policy = _FakeWorkflowPolicy(registered={workflow_id}, published={workflow_id: 2})
    service, versions = _service(policy)
    employee = await service.create(
        tenant_id=tenant_id,
        created_by=user_id,
        draft=_draft(RuntimeType.WORKFLOW, workflow_id),
    )
    await service.publish(tenant_id=tenant_id, employee_id=employee.id, published_by=user_id)

    version = versions.versions[0]
    assert version.definition["workflow_id"] == str(workflow_id)  # type: ignore[attr-defined]
    assert version.definition["workflow_version"] == 2  # type: ignore[attr-defined]

    # 工作流回滚到 v1 后，已发布的员工版本仍固定引用 v2。
    policy.set_published(workflow_id, 1)
    assert version.definition["workflow_version"] == 2  # type: ignore[attr-defined]
