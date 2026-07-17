"""固定/混合工作流执行内核：从平台图定义编译 LangGraph StateGraph 并驱动。

只用 LangGraph/Deep Agents 公开扩展点（StateGraph/add_node/add_conditional_edges/
interrupt/Command/子图），不触碰任何私有 API。
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType
from agent_platform.platform.workflows.graph_spec import parse_workflow_graph
from agent_platform.runtimes.base import RuntimeStartRequest
from agent_platform.runtimes.workflow_graph import (
    WorkflowNodeDependencies,
    _render_prompt,
    build_workflow_runtime,
)


class StubModel:
    """最小可用的 ainvoke 模型替身：把最后一条消息内容回声为大写。"""

    def __init__(self, reply: str | None = None) -> None:
        self._reply = reply

    async def ainvoke(self, messages: Any, *args: Any, **kwargs: Any) -> AIMessage:
        if self._reply is not None:
            return AIMessage(content=self._reply)
        last = messages[-1]
        content = last.content if hasattr(last, "content") else str(last)
        return AIMessage(content=f"agent:{content}")


def _tool(name: str, fn: Callable[..., Awaitable[str]]) -> StructuredTool:
    return StructuredTool.from_function(coroutine=fn, name=name, description=name)


def _deps(
    *,
    model: Any | None = None,
    tools: dict[str, Any] | None = None,
    subagent: Callable[..., Awaitable[str]] | None = None,
) -> WorkflowNodeDependencies:
    async def default_subagent(**kwargs: Any) -> str:
        return "subagent-result"

    return WorkflowNodeDependencies(
        model=model or StubModel(),
        tools_by_name=tools or {},
        subagent_runner=subagent or default_subagent,
    )


def _request(input_data: dict[str, object]) -> RuntimeStartRequest:
    return RuntimeStartRequest(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        employee_id=uuid4(),
        thread_id=f"thread-{uuid4()}",
        employee_definition={"name": "wf"},
        input_data=input_data,
    )


@pytest.mark.asyncio
async def test_linear_agent_workflow_runs_to_completion() -> None:
    spec = parse_workflow_graph(
        {
            "entrypoint": "greet",
            "nodes": [
                {
                    "name": "greet",
                    "type": "agent",
                    "config": {"prompt": "hello"},
                    "next": None,
                }
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec, deps=_deps(model=StubModel(reply="done")), checkpointer=InMemorySaver()
    )
    request = _request({"topic": "x"})
    state = await runtime.start(request)
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "done"
    history = await runtime.get_history(request.run_id)
    assert any(event.type is EventType.RUN_COMPLETED for event in history)


@pytest.mark.asyncio
async def test_tool_node_invokes_bound_tool() -> None:
    calls: list[dict[str, object]] = []

    async def send_email(recipient: str) -> str:
        calls.append({"recipient": recipient})
        return f"sent:{recipient}"

    spec = parse_workflow_graph(
        {
            "entrypoint": "notify",
            "nodes": [
                {
                    "name": "notify",
                    "type": "tool",
                    "config": {"tool": "send_email", "arguments": {"recipient": "a@b.c"}},
                    "next": None,
                }
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec,
        deps=_deps(tools={"send_email": _tool("send_email", send_email)}),
        checkpointer=InMemorySaver(),
    )
    request = _request({})
    state = await runtime.start(request)
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "sent:a@b.c"
    assert calls == [{"recipient": "a@b.c"}]


@pytest.mark.asyncio
async def test_tool_node_missing_tool_fails_closed() -> None:
    spec = parse_workflow_graph(
        {
            "entrypoint": "notify",
            "nodes": [
                {
                    "name": "notify",
                    "type": "tool",
                    "config": {"tool": "ghost_tool", "arguments": {}},
                    "next": None,
                }
            ],
        }
    )
    runtime = build_workflow_runtime(spec=spec, deps=_deps(), checkpointer=InMemorySaver())
    request = _request({})
    state = await runtime.start(request)
    assert state.status is RunStatus.FAILED


@pytest.mark.asyncio
async def test_branch_routes_on_state_key() -> None:
    async def vip_tool() -> str:
        return "vip-handled"

    async def normal_tool() -> str:
        return "normal-handled"

    spec = parse_workflow_graph(
        {
            "entrypoint": "classify",
            "nodes": [
                {
                    "name": "classify",
                    "type": "agent",
                    "config": {"prompt": "classify", "output_key": "tier"},
                    "next": "route",
                },
                {
                    "name": "route",
                    "type": "branch",
                    "config": {"state_key": "tier"},
                    "routes": {"vip": "vip", "normal": "normal"},
                    "default": "normal",
                },
                {"name": "vip", "type": "tool", "config": {"tool": "vip_tool"}, "next": None},
                {
                    "name": "normal",
                    "type": "tool",
                    "config": {"tool": "normal_tool"},
                    "next": None,
                },
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec,
        deps=_deps(
            model=StubModel(reply="vip"),
            tools={
                "vip_tool": _tool("vip_tool", vip_tool),
                "normal_tool": _tool("normal_tool", normal_tool),
            },
        ),
        checkpointer=InMemorySaver(),
    )
    state = await runtime.start(_request({}))
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "vip-handled"


@pytest.mark.asyncio
async def test_branch_unmatched_without_default_ends_gracefully() -> None:
    """分支无 default、上游值未命中任何 route 时应优雅终止（正常完成），而非 KeyError→FAILED。"""

    async def vip_tool() -> str:
        return "vip-handled"

    spec = parse_workflow_graph(
        {
            "entrypoint": "classify",
            "nodes": [
                {
                    "name": "classify",
                    "type": "agent",
                    "config": {"prompt": "classify", "output_key": "tier"},
                    "next": "route",
                },
                {
                    "name": "route",
                    "type": "branch",
                    "config": {"state_key": "tier"},
                    "routes": {"vip": "vip"},
                    "default": None,
                },
                {"name": "vip", "type": "tool", "config": {"tool": "vip_tool"}, "next": None},
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec,
        deps=_deps(
            # 上游输出 "normal"，未命中 routes={"vip": ...} 且无 default。
            model=StubModel(reply="normal"),
            tools={"vip_tool": _tool("vip_tool", vip_tool)},
        ),
        checkpointer=InMemorySaver(),
    )
    state = await runtime.start(_request({}))
    assert state.status is RunStatus.COMPLETED


def test_render_prompt_does_not_traverse_attributes() -> None:
    """作者可控模板不得走 str.format 的属性遍历语义，也不得因 AttributeError 崩溃。"""

    state: dict[str, object] = {"input": {"topic": "x"}, "values": {"y": 1}}
    # {input.__class__} / {input.foo} 不应被求值为类信息或抛异常。
    rendered = _render_prompt("A {input.__class__} B {input.foo} C {input}", state)  # type: ignore[arg-type]
    assert "__class__" not in rendered.replace("{input.__class__}", "")
    assert "type" not in rendered.lower() or "{input.__class__}" in rendered
    # 未知点号占位保持字面量，不遍历属性。
    assert "{input.__class__}" in rendered
    assert "{input.foo}" in rendered
    # 白名单占位 {input} 正常替换为 JSON。
    assert '"topic": "x"' in rendered


def test_render_prompt_does_not_reinject_values_from_input_literal() -> None:
    """单遍替换：input 数据里字面出现的 {values} 不得触发 values 注入。

    模板作者只暴露 {input}、故意不含 {values}；若顺序 replace，先内联 input 后
    第二遍会把 input 里的字面 {values} 换成上游节点输出，绕过作者意图。
    """

    state: dict[str, object] = {
        "input": {"note": "see {values}"},
        "values": {"secret": "leak"},
    }
    rendered = _render_prompt("Task: {input}", state)  # type: ignore[arg-type]
    # input 里的字面 {values} 必须原样保留，绝不内联上游 values 数据。
    assert "{values}" in rendered
    assert "leak" not in rendered


@pytest.mark.asyncio
async def test_retry_recovers_from_transient_failure() -> None:
    attempts = {"count": 0}

    async def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("transient")
        return "recovered"

    spec = parse_workflow_graph(
        {
            "entrypoint": "flaky",
            "nodes": [
                {
                    "name": "flaky",
                    "type": "tool",
                    "config": {"tool": "flaky"},
                    "retry": {"max_attempts": 3},
                    "next": None,
                }
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec,
        deps=_deps(tools={"flaky": _tool("flaky", flaky)}),
        checkpointer=InMemorySaver(),
    )
    state = await runtime.start(_request({}))
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "recovered"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_subflow_node_runs_nested_graph() -> None:
    spec = parse_workflow_graph(
        {
            "entrypoint": "outer",
            "nodes": [
                {
                    "name": "outer",
                    "type": "subflow",
                    "config": {
                        "graph": {
                            "entrypoint": "inner",
                            "nodes": [
                                {
                                    "name": "inner",
                                    "type": "agent",
                                    "config": {"prompt": "inner"},
                                    "next": None,
                                }
                            ],
                        }
                    },
                    "next": None,
                }
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec, deps=_deps(model=StubModel(reply="nested")), checkpointer=InMemorySaver()
    )
    state = await runtime.start(_request({}))
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "nested"


@pytest.mark.asyncio
async def test_subagent_node_invokes_deep_agent_runner() -> None:
    seen: dict[str, object] = {}

    async def runner(*, prompt: str, input_data: dict[str, object], node_name: str) -> str:
        seen["prompt"] = prompt
        seen["node"] = node_name
        return "deep-result"

    spec = parse_workflow_graph(
        {
            "entrypoint": "delegate",
            "nodes": [
                {
                    "name": "delegate",
                    "type": "subagent",
                    "config": {"prompt": "do deep work"},
                    "next": None,
                }
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec, deps=_deps(subagent=runner), checkpointer=InMemorySaver()
    )
    state = await runtime.start(_request({"x": 1}))
    assert state.status is RunStatus.COMPLETED
    assert state.data["output"] == "deep-result"
    assert seen["prompt"] == "do deep work"
    assert seen["node"] == "delegate"


@pytest.mark.asyncio
async def test_human_approval_node_waits_then_continues_on_approve() -> None:
    side_effects = {"count": 0}

    async def act() -> str:
        side_effects["count"] += 1
        return "acted"

    spec = parse_workflow_graph(
        {
            "entrypoint": "review",
            "nodes": [
                {
                    "name": "review",
                    "type": "human_approval",
                    "config": {"title": "请审批"},
                    "next": "act",
                },
                {"name": "act", "type": "tool", "config": {"tool": "act"}, "next": None},
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec, deps=_deps(tools={"act": _tool("act", act)}), checkpointer=InMemorySaver()
    )
    request = _request({})
    state = await runtime.start(request)
    assert state.status is RunStatus.WAITING_FOR_APPROVAL
    assert side_effects["count"] == 0
    history = await runtime.get_history(request.run_id)
    approval_events = [e for e in history if e.type is EventType.APPROVAL_REQUIRED]
    assert len(approval_events) == 1
    approval_id = runtime.pending_approval_id(request.run_id)
    assert approval_id is not None

    await runtime.approve(request.run_id, approval_id)
    final = await runtime.get_state(request.run_id)
    assert final.status is RunStatus.COMPLETED
    assert final.data["output"] == "acted"
    assert side_effects["count"] == 1


@pytest.mark.asyncio
async def test_human_approval_reject_stops_before_downstream_side_effects() -> None:
    side_effects = {"count": 0}

    async def act() -> str:
        side_effects["count"] += 1
        return "acted"

    spec = parse_workflow_graph(
        {
            "entrypoint": "review",
            "nodes": [
                {
                    "name": "review",
                    "type": "human_approval",
                    "config": {"title": "请审批"},
                    "next": "act",
                },
                {"name": "act", "type": "tool", "config": {"tool": "act"}, "next": None},
            ],
        }
    )
    runtime = build_workflow_runtime(
        spec=spec, deps=_deps(tools={"act": _tool("act", act)}), checkpointer=InMemorySaver()
    )
    request = _request({})
    state = await runtime.start(request)
    assert state.status is RunStatus.WAITING_FOR_APPROVAL
    approval_id = runtime.pending_approval_id(request.run_id)
    assert approval_id is not None

    await runtime.reject(request.run_id, approval_id, reason="不合规")
    final = await runtime.get_state(request.run_id)
    assert final.status is RunStatus.CANCELLED
    # 拒绝必须阻断审批节点之后的工具副作用。
    assert side_effects["count"] == 0
