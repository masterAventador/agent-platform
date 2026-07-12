from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from langchain_core.tools import BaseTool
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.infrastructure.database.repositories.skills import (
    SqlAlchemySkillRepository,
)
from agent_platform.infrastructure.database.repositories.tools import (
    SqlAlchemyToolRepository,
)
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.skills.materializer import SkillMaterializer
from agent_platform.platform.skills.ports import SkillStorage
from agent_platform.platform.tool_gateway import PolicyContext
from agent_platform.runtimes.base import (
    EmployeeRuntime,
    RunWorkspace,
    RunWorkspaceFactory,
)
from agent_platform.runtimes.tool_gateway_adapter import (
    InvocationContext,
    ToolGatewayAdapter,
    ToolGatewayInvoker,
)

RuntimeWorkMode = Literal["autonomous", "workflow", "hybrid"]


class UntrustedRuntimeDefinition(Exception):
    """发布定义包含只能由运行时准备层生成的内部字段。"""


@dataclass(frozen=True, slots=True)
class PublishedRuntimeCapabilities:
    """从已发布员工版本读取的可信运行时能力白名单。"""

    work_mode: RuntimeWorkMode
    skill_ids: tuple[UUID, ...]
    tool_ids: tuple[UUID, ...]

    @classmethod
    def from_definition(
        cls,
        definition: dict[str, object],
    ) -> "PublishedRuntimeCapabilities":
        return cls(
            work_mode=TypeAdapter(RuntimeWorkMode).validate_python(definition["work_mode"]),
            skill_ids=tuple(
                TypeAdapter(list[UUID]).validate_python(definition.get("skill_ids", []))
            ),
            tool_ids=tuple(
                TypeAdapter(list[UUID]).validate_python(definition.get("tool_ids", []))
            ),
        )


@dataclass(frozen=True, slots=True)
class PreparedRuntimeResult:
    runtime: EmployeeRuntime
    employee_definition: dict[str, JsonValue]


RuntimeFactory = Callable[[Sequence[BaseTool], RunWorkspace], EmployeeRuntime]


class PlatformRuntimeSelector:
    """把自主员工交给 DeepAgent factory，其余流程交给 LangGraph factory。"""

    def __init__(
        self,
        *,
        autonomous_factory: RuntimeFactory,
        workflow_factory: RuntimeFactory,
    ) -> None:
        self._autonomous_factory = autonomous_factory
        self._workflow_factory = workflow_factory

    def select(
        self,
        *,
        work_mode: RuntimeWorkMode,
        tools: Sequence[BaseTool],
        workspace: RunWorkspace,
    ) -> EmployeeRuntime:
        factory = (
            self._autonomous_factory
            if work_mode == "autonomous"
            else self._workflow_factory
        )
        return factory(tools, workspace)


class ComposedRuntimeResolver:
    """按发布版本白名单完成 Skill、Tool 与执行内核的一次性装配。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        skill_storage: SkillStorage,
        workspace_factory: RunWorkspaceFactory,
        gateway: ToolGatewayInvoker,
        runtime_selector: PlatformRuntimeSelector,
    ) -> None:
        self._session_factory = session_factory
        self._skill_storage = skill_storage
        self._workspace_factory = workspace_factory
        self._gateway = gateway
        self._runtime_selector = runtime_selector

    async def resolve(
        self,
        run: Run,
        definition: dict[str, object],
    ) -> PreparedRuntimeResult:
        if "skill_paths" in definition:
            raise UntrustedRuntimeDefinition(
                "skill_paths must be supplied by runtime preparation"
            )
        capabilities = PublishedRuntimeCapabilities.from_definition(definition)
        workspace = await self._workspace_factory.create(
            run_id=run.id,
            tenant_id=run.tenant_id,
            user_id=run.created_by,
            employee_id=run.employee_id,
            thread_id=run.thread_id,
        )
        async with self._session_factory() as session:
            skill_paths = await SkillMaterializer(
                repository=SqlAlchemySkillRepository(session),
                storage=self._skill_storage,
            ).materialize(
                tenant_id=run.tenant_id,
                skill_ids=capabilities.skill_ids,
                workspace=workspace,
            )
            tool_repository = SqlAlchemyToolRepository(session)
            tool_metadata = []
            for tool_id in capabilities.tool_ids:
                tool = await tool_repository.get_tool(
                    tenant_id=run.tenant_id,
                    tool_id=tool_id,
                )
                if tool is None or not tool.enabled:
                    raise UntrustedRuntimeDefinition("published tool is unavailable")
                tool_metadata.append(tool)

        gateway_adapter = ToolGatewayAdapter(
            gateway=self._gateway,
            invocation_context=InvocationContext(
                tenant_id=run.tenant_id,
                run_id=run.id,
                employee_id=run.employee_id,
                user_id=run.created_by,
            ),
            policy_context=PolicyContext(
                allowed_tool_ids=frozenset(capabilities.tool_ids),
            ),
        )
        tools = [gateway_adapter.adapt(metadata) for metadata in tool_metadata]
        runtime = self._runtime_selector.select(
            work_mode=capabilities.work_mode,
            tools=tools,
            workspace=workspace,
        )
        return PreparedRuntimeResult(
            runtime=runtime,
            employee_definition=extend_runtime_definition(
                definition,
                skill_paths=skill_paths,
            ),
        )


def extend_runtime_definition(
    definition: dict[str, object],
    *,
    skill_paths: list[str],
) -> dict[str, JsonValue]:
    """仅以准备层的物化结果扩展发布定义，不接受持久化路径注入。"""

    if "skill_paths" in definition:
        raise UntrustedRuntimeDefinition("skill_paths must be supplied by runtime preparation")
    trusted_definition = TypeAdapter(dict[str, JsonValue]).validate_python(definition)
    return {**trusted_definition, "skill_paths": list(skill_paths)}
