from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import JsonValue

from agent_platform.runtimes.base import RuntimeStartRequest
from agent_platform.runtimes.deep_agent import DeepAgentFactory, DeepAgentRuntime


class ToolBindingFakeChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del tools, tool_choice, kwargs
        return self


class CapturingAgentGraph:
    async def ainvoke(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
    ) -> Mapping[str, object]:
        del input_data, config
        return {"messages": []}


def test_official_deep_agent_factory_passes_skill_paths_to_public_parameter() -> None:
    captured: dict[str, object] = {}

    def agent_builder(**kwargs: object) -> object:
        captured.update(kwargs)
        return CapturingAgentGraph()

    skill_paths: list[JsonValue] = [
        "/workspace/skills/report-writer",
        "/workspace/skills/research",
    ]
    factory = DeepAgentFactory(
        model="test-model",
        tools=[],
        agent_builder=agent_builder,
    )
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        employee_id=uuid4(),
        thread_id="skill-enabled-deep-agent",
        employee_definition={
            "system_prompt": "按 Skill 执行任务",
            "skill_paths": skill_paths,
        },
        input_data={"message": "开始"},
    )

    graph = factory(request)

    assert isinstance(graph, CapturingAgentGraph)
    assert captured["skills"] == skill_paths


@pytest.mark.asyncio
async def test_official_deep_agent_factory_runs_with_injected_model() -> None:
    model = ToolBindingFakeChatModel(messages=iter(["官方 Deep Agents 调用成功"]))
    runtime = DeepAgentRuntime(agent_factory=DeepAgentFactory(model=model, tools=[]))
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        employee_id=uuid4(),
        thread_id="official-deep-agent",
        employee_definition={"system_prompt": "返回测试结果"},
        input_data={"message": "开始"},
    )

    state = await runtime.start(request)

    assert state.status.value == "completed", await runtime.get_history(request.run_id)
    assert state.data == {"output": "官方 Deep Agents 调用成功"}
