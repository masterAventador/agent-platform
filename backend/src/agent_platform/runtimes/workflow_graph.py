"""把平台工作流图定义编译为 LangGraph ``StateGraph`` 的执行内核。

零侵入约束：只使用 LangGraph 公开扩展点——``StateGraph``、``add_node``（含公开的
``retry_policy``）、``add_conditional_edges``、``interrupt``、``Command`` 与编译后的子图；
Deep Agents 子智能体通过注入的 ``subagent_runner`` 回调调用（在 worker 装配层用官方
``DeepAgentFactory`` 实现）。不导入任何私有模块，不复制框架内部实现。

平台事件与运行状态仍由既有 :class:`LangGraphRuntime` 统一映射，图内部结构不外泄。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, interrupt
from pydantic import JsonValue, TypeAdapter

from agent_platform.platform.workflows.graph_spec import (
    END_SENTINEL,
    WorkflowGraphSpec,
    WorkflowNode,
    WorkflowNodeType,
)
from agent_platform.runtimes.base import RuntimeStartRequest
from agent_platform.runtimes.langgraph import LangGraphAgentGraph, LangGraphRuntime

SubagentRunner = Callable[..., Awaitable[str]]


class WorkflowInvalidDefinition(Exception):
    """图定义在编译期被判定为不可运行（如子流程内嵌人工节点）。"""


@dataclass(frozen=True, slots=True)
class WorkflowNodeDependencies:
    """一次 run 的节点执行依赖，由 worker 装配层按发布版本白名单提供。"""

    model: BaseChatModel
    tools_by_name: Mapping[str, BaseTool]
    subagent_runner: SubagentRunner


class WorkflowState(TypedDict, total=False):
    input: dict[str, JsonValue]
    values: dict[str, JsonValue]
    output: JsonValue


def _output_key(node: WorkflowNode) -> str:
    key = node.config.get("output_key")
    if isinstance(key, str) and key:
        return key
    return node.name


def _target(node: WorkflowNode) -> str:
    if node.next is None or node.next == END_SENTINEL:
        return END
    return node.next


def _store(node: WorkflowNode, state: WorkflowState, result: JsonValue) -> dict[str, JsonValue]:
    values = dict(state.get("values") or {})
    values[_output_key(node)] = result
    update: dict[str, JsonValue] = {"values": values}
    if node.is_terminal:
        update["output"] = result
    return update


def _render_prompt(prompt: str, state: WorkflowState) -> str:
    """按白名单占位符做纯字符串替换。

    模板由 EMPLOYEES_MANAGE 作者可控，绝不能走 ``str.format`` 的属性/索引遍历语义
    （``{input.__class__...}`` 会泄露对象内部）。只支持 ``{input}`` / ``{values}`` 两个
    占位符，替换为其 JSON 表示；其余含点号/属性的占位一律保持字面量，不求值、不遍历。
    """

    input_data = state.get("input") or {}
    values = state.get("values") or {}
    rendered = prompt.replace(
        "{input}", json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str)
    )
    rendered = rendered.replace(
        "{values}", json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    )
    return rendered


def _coerce_str(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(value)


class _WorkflowGraphBuilder:
    def __init__(self, deps: WorkflowNodeDependencies) -> None:
        self._deps = deps

    def compile(
        self,
        spec: WorkflowGraphSpec,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None,
        allow_interrupts: bool = True,
    ) -> Any:
        builder: Any = StateGraph(WorkflowState)
        for node in spec.nodes:
            if node.type is WorkflowNodeType.HUMAN_APPROVAL and not allow_interrupts:
                raise WorkflowInvalidDefinition(
                    "子流程内不支持人工审批节点（Interrupt 语义无法在子图 ainvoke 中安全暂停）"
                )
            builder.add_node(
                node.name,
                self._executor(node),
                retry_policy=(
                    # 节点显式声明 retry 即视为「该节点可能瞬时失败」，对所有异常重试
                    # （LangGraph 默认 retry_on 会跳过 RuntimeError 等，语义与显式 opt-in 不符）。
                    RetryPolicy(max_attempts=node.retry.max_attempts, retry_on=(Exception,))
                    if node.retry is not None
                    else None
                ),
            )
        builder.add_edge(START, spec.entrypoint)
        for node in spec.nodes:
            self._wire_edges(builder, node)
        return builder.compile(checkpointer=checkpointer)

    def _wire_edges(self, builder: Any, node: WorkflowNode) -> None:
        if node.type is WorkflowNodeType.BRANCH:
            self._wire_branch(builder, node)
            return
        if node.type is WorkflowNodeType.HUMAN_APPROVAL:
            # 人工审批节点用 Command(goto) 自行路由（批准→next，拒绝→END），不加静态边。
            return
        builder.add_edge(node.name, _target(node))

    def _wire_branch(self, builder: Any, node: WorkflowNode) -> None:
        state_key = _coerce_str(node.config["state_key"])
        routes = dict(node.routes)
        default = node.default

        def router(state: WorkflowState) -> str:
            values = state.get("values") or {}
            raw = values.get(state_key)
            key = _coerce_str(raw) if raw is not None else None
            if key is not None and key in routes:
                target = routes[key]
            elif default is not None:
                target = default
            else:
                target = END_SENTINEL
            return END if target == END_SENTINEL else target

        # 无条件包含 END：无 default 且上游值未命中任一 route 时，router 优雅回退到
        # END（正常完成），否则 LangGraph Branch 找不到 END 键会抛 KeyError → run FAILED。
        path_map: dict[str, str] = {END: END}
        for target in {*routes.values(), *([default] if default is not None else [])}:
            resolved = END if target == END_SENTINEL else target
            path_map[resolved] = resolved
        builder.add_conditional_edges(node.name, router, path_map)

    def _executor(self, node: WorkflowNode) -> Callable[[WorkflowState], Awaitable[Any]]:
        if node.type is WorkflowNodeType.AGENT:
            return self._agent_executor(node)
        if node.type is WorkflowNodeType.TOOL:
            return self._tool_executor(node)
        if node.type is WorkflowNodeType.SUBAGENT:
            return self._subagent_executor(node)
        if node.type is WorkflowNodeType.HUMAN_APPROVAL:
            return self._approval_executor(node)
        if node.type is WorkflowNodeType.SUBFLOW:
            return self._subflow_executor(node)
        if node.type is WorkflowNodeType.BRANCH:
            return self._branch_executor(node)
        raise WorkflowInvalidDefinition(f"未知节点类型：{node.type}")

    def _agent_executor(
        self, node: WorkflowNode
    ) -> Callable[[WorkflowState], Awaitable[Any]]:
        prompt = _coerce_str(node.config.get("prompt", ""))
        system = node.config.get("system")

        async def run(state: WorkflowState) -> dict[str, JsonValue]:
            messages: list[Any] = []
            if isinstance(system, str) and system:
                messages.append(SystemMessage(content=system))
            messages.append(HumanMessage(content=_render_prompt(prompt, state)))
            result = await self._deps.model.ainvoke(messages)
            text = _coerce_str(getattr(result, "content", result))
            return _store(node, state, text)

        return run

    def _tool_executor(
        self, node: WorkflowNode
    ) -> Callable[[WorkflowState], Awaitable[Any]]:
        tool_name = _coerce_str(node.config.get("tool", ""))
        raw_arguments = node.config.get("arguments", {})
        arguments = raw_arguments if isinstance(raw_arguments, Mapping) else {}

        async def run(state: WorkflowState) -> dict[str, JsonValue]:
            tool = self._deps.tools_by_name.get(tool_name)
            if tool is None:
                raise WorkflowInvalidDefinition(f"工具 {tool_name} 未在本次运行绑定")
            result = await tool.ainvoke(dict(arguments))
            return _store(node, state, _coerce_str(result))

        return run

    def _subagent_executor(
        self, node: WorkflowNode
    ) -> Callable[[WorkflowState], Awaitable[Any]]:
        prompt = _coerce_str(node.config.get("prompt", ""))

        async def run(state: WorkflowState) -> dict[str, JsonValue]:
            result = await self._deps.subagent_runner(
                prompt=_render_prompt(prompt, state),
                input_data=dict(state.get("input") or {}),
                node_name=node.name,
            )
            return _store(node, state, _coerce_str(result))

        return run

    def _approval_executor(
        self, node: WorkflowNode
    ) -> Callable[[WorkflowState], Awaitable[Any]]:
        # 用确定性 approval_id 让平台事件与恢复语义稳定（与 LangGraphRuntime 对齐）。
        approval_id = uuid5(NAMESPACE_URL, f"agent-platform-workflow-approval:{node.name}")
        context = node.config

        async def run(state: WorkflowState) -> Command[Any]:
            decision = interrupt(
                {
                    "kind": "approval",
                    "approval_id": str(approval_id),
                    "node": node.name,
                    "context": context,
                }
            )
            action = decision.get("action") if isinstance(decision, Mapping) else None
            values = dict(state.get("values") or {})
            values[_output_key(node)] = _coerce_str(action)
            if action == "reject":
                return Command(goto=END, update={"values": values})
            return Command(goto=_target(node), update={"values": values})

        return run

    def _subflow_executor(
        self, node: WorkflowNode
    ) -> Callable[[WorkflowState], Awaitable[Any]]:
        subgraph = self.compile(
            node.subflow_spec(),
            checkpointer=None,
            allow_interrupts=False,
        )

        async def run(state: WorkflowState) -> dict[str, JsonValue]:
            result = await subgraph.ainvoke(
                {"input": dict(state.get("input") or {}), "values": {}}
            )
            output: JsonValue = TypeAdapter(JsonValue).validate_python(result.get("output"))
            return _store(node, state, output)

        return run

    def _branch_executor(
        self, node: WorkflowNode
    ) -> Callable[[WorkflowState], Awaitable[Any]]:
        async def run(state: WorkflowState) -> dict[str, JsonValue]:
            # 分支节点自身不产出，只做透传，路由由 add_conditional_edges 决定。
            del state
            return {}

        return run


def compile_workflow_graph(
    spec: WorkflowGraphSpec,
    deps: WorkflowNodeDependencies,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> Any:
    return _WorkflowGraphBuilder(deps).compile(spec, checkpointer=checkpointer)


def build_workflow_runtime(
    *,
    spec: WorkflowGraphSpec,
    deps: WorkflowNodeDependencies,
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> LangGraphRuntime:
    """把工作流图编译成一次可复用的图，包进平台统一的 LangGraphRuntime。"""

    graph: LangGraphAgentGraph = compile_workflow_graph(spec, deps, checkpointer=checkpointer)

    def graph_factory(request: RuntimeStartRequest) -> LangGraphAgentGraph:
        del request
        return graph

    return LangGraphRuntime(graph_factory=graph_factory)
