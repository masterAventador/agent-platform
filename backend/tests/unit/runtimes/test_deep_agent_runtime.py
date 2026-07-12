from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from agent_platform.platform.runs.events import EventType
from agent_platform.runtimes.base import EmployeeRuntime, RuntimeStartRequest
from agent_platform.runtimes.deep_agent import DeepAgentRuntime


class FakeAgentGraph:
    async def ainvoke(
        self,
        input_data: dict[str, object],
        config: dict[str, object],
    ) -> dict[str, Any]:
        del input_data, config
        return {
            "messages": [AIMessage(content="这是平台允许输出的答案")],
            "private_graph_state": object(),
        }


@pytest.mark.asyncio
async def test_deep_agent_runtime_maps_result_to_platform_contract() -> None:
    runtime = DeepAgentRuntime(agent_factory=lambda request: FakeAgentGraph())
    assert isinstance(runtime, EmployeeRuntime)
    request = RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        employee_id=uuid4(),
        thread_id="thread-deep-agent",
        employee_definition={"system_prompt": "你是研究助理"},
        input_data={"topic": "Deep Agents"},
    )

    state = await runtime.start(request)
    history = await runtime.get_history(request.run_id)

    assert state.status.value == "completed"
    assert state.data == {"output": "这是平台允许输出的答案"}
    assert [event.type for event in history] == [
        EventType.RUN_STARTED,
        EventType.MESSAGE_OUTPUT,
        EventType.RUN_COMPLETED,
    ]
    assert [event.sequence for event in history] == [1, 2, 3]
    assert "private_graph_state" not in str([event.model_dump() for event in history])
    streamed_events = [
        event async for event in runtime.stream(request.run_id, after_sequence=1)
    ]
    assert streamed_events == history[1:]
