from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.platform.runs.entities import Run
from agent_platform.workers.runtime_composition import (
    ComposedRuntimeResolver,
    PlatformRuntimeSelector,
    PublishedRuntimeCapabilities,
    UntrustedRuntimeDefinition,
    extend_runtime_definition,
)


class EmptyStorage:
    async def get(self, *, key: str) -> bytes:
        raise AssertionError(key)


class RecordingWorkspace:
    async def write_file(self, *, path: str, content: bytes) -> None:
        raise AssertionError((path, content))


class RecordingWorkspaceFactory:
    def __init__(self) -> None:
        self.identity: dict[str, object] | None = None

    async def create(self, **identity):
        self.identity = identity
        return RecordingWorkspace()


class RecordingSelector:
    def __init__(self) -> None:
        self.selection: tuple[object, ...] | None = None
        self.runtime = object()

    def select(self, *, work_mode, tools, workspace):
        self.selection = (work_mode, tools, workspace)
        return self.runtime


class UnusedGateway:
    async def invoke(self, invocation, context):
        raise AssertionError((invocation, context))


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_published_capabilities_parse_only_version_bound_skill_and_tool_ids() -> None:
    skill_id = uuid4()
    tool_id = uuid4()

    capabilities = PublishedRuntimeCapabilities.from_definition(
        {
            "work_mode": "autonomous",
            "skill_ids": [str(skill_id)],
            "tool_ids": [str(tool_id)],
        }
    )

    assert capabilities.work_mode == "autonomous"
    assert capabilities.skill_ids == (skill_id,)
    assert capabilities.tool_ids == (tool_id,)


def test_prepare_layer_is_the_only_source_of_runtime_skill_paths() -> None:
    definition = {"work_mode": "autonomous", "skill_ids": [], "tool_ids": []}

    prepared = extend_runtime_definition(
        definition,
        skill_paths=["/skills/trusted/version"],
    )

    assert prepared == {
        **definition,
        "skill_paths": ["/skills/trusted/version"],
    }
    assert "skill_paths" not in definition


def test_published_definition_cannot_supply_runtime_skill_paths() -> None:
    with pytest.raises(UntrustedRuntimeDefinition):
        extend_runtime_definition(
            {
                "work_mode": "autonomous",
                "skill_ids": [],
                "tool_ids": [],
                "skill_paths": ["/host/untrusted"],
            },
            skill_paths=["/skills/trusted/version"],
        )


@pytest.mark.asyncio
async def test_composed_resolver_prepares_workspace_before_selecting_runtime(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    workspaces = RecordingWorkspaceFactory()
    selector = RecordingSelector()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        workspace_factory=workspaces,
        gateway=UnusedGateway(),
        runtime_selector=selector,
    )

    prepared = await resolver.resolve(
        run,
        {"work_mode": "workflow", "skill_ids": [], "tool_ids": []},
    )

    assert workspaces.identity == {
        "run_id": run.id,
        "tenant_id": run.tenant_id,
        "user_id": run.created_by,
        "employee_id": run.employee_id,
        "thread_id": run.thread_id,
    }
    assert selector.selection is not None
    assert selector.selection[0] == "workflow"
    assert selector.selection[1] == []
    assert prepared.runtime is selector.runtime
    assert prepared.employee_definition["skill_paths"] == []


@pytest.mark.asyncio
async def test_composed_resolver_rejects_persisted_skill_paths_before_workspace_side_effect(
    session_factory,
) -> None:
    run = Run.create(
        tenant_id=uuid4(),
        employee_id=uuid4(),
        employee_version=1,
        created_by=uuid4(),
        input_data={},
    )
    workspaces = RecordingWorkspaceFactory()
    resolver = ComposedRuntimeResolver(
        session_factory=session_factory,
        skill_storage=EmptyStorage(),
        workspace_factory=workspaces,
        gateway=UnusedGateway(),
        runtime_selector=RecordingSelector(),
    )

    with pytest.raises(UntrustedRuntimeDefinition):
        await resolver.resolve(
            run,
            {
                "work_mode": "autonomous",
                "skill_ids": [],
                "tool_ids": [],
                "skill_paths": ["/host/untrusted"],
            },
        )

    assert workspaces.identity is None


def test_runtime_selector_maps_autonomous_and_workflow_to_distinct_factories() -> None:
    calls: list[str] = []
    selector = PlatformRuntimeSelector(
        autonomous_factory=lambda tools, workspace: calls.append("deep-agent") or object(),
        workflow_factory=lambda tools, workspace: calls.append("langgraph") or object(),
    )

    selector.select(work_mode="autonomous", tools=[], workspace=RecordingWorkspace())
    selector.select(work_mode="workflow", tools=[], workspace=RecordingWorkspace())
    selector.select(work_mode="hybrid", tools=[], workspace=RecordingWorkspace())

    assert calls == ["deep-agent", "langgraph", "langgraph"]
