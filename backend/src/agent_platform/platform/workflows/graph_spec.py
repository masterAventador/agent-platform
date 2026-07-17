"""固定工作流的图定义与静态校验。

图定义是平台自研的稳定协议，与 LangGraph 内部结构解耦：它描述节点、条件分支、
重试、子流程、人工审批与 Interrupt，由 ``runtimes`` 适配层翻译成 LangGraph
``StateGraph``。这里只做纯静态校验（结构、可达性、无环、分支目标），不依赖任何
框架运行时对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

END_SENTINEL = "__end__"
MAX_RETRY_ATTEMPTS = 10
MAX_NODES = 100
MAX_SUBFLOW_DEPTH = 3


class WorkflowNodeType(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    SUBAGENT = "subagent"
    HUMAN_APPROVAL = "human_approval"
    BRANCH = "branch"
    SUBFLOW = "subflow"


@dataclass(frozen=True, slots=True)
class WorkflowGraphIssue:
    path: tuple[str, ...]
    message: str


class InvalidWorkflowGraph(Exception):
    """工作流图定义未通过静态校验。"""

    def __init__(self, issue: WorkflowGraphIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


class RetryPolicySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(ge=1, le=MAX_RETRY_ATTEMPTS)


class WorkflowNodeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    type: WorkflowNodeType
    config: dict[str, JsonValue] = Field(default_factory=dict)
    retry: RetryPolicySpec | None = None
    next: str | None = None
    routes: dict[str, str] = Field(default_factory=dict)
    default: str | None = None


class WorkflowGraphModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entrypoint: str = Field(min_length=1, max_length=100)
    nodes: list[WorkflowNodeModel] = Field(min_length=1, max_length=MAX_NODES)


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    name: str
    type: WorkflowNodeType
    config: dict[str, JsonValue]
    retry: RetryPolicySpec | None
    next: str | None
    routes: dict[str, str]
    default: str | None

    @property
    def is_terminal(self) -> bool:
        return self.type is not WorkflowNodeType.BRANCH and (
            self.next is None or self.next == END_SENTINEL
        )

    def linear_targets(self) -> set[str]:
        if self.type is WorkflowNodeType.BRANCH:
            return set()
        if self.next is None or self.next == END_SENTINEL:
            return set()
        return {self.next}

    def branch_targets(self) -> set[str]:
        if self.type is not WorkflowNodeType.BRANCH:
            return set()
        targets = {target for target in self.routes.values() if target != END_SENTINEL}
        if self.default is not None and self.default != END_SENTINEL:
            targets.add(self.default)
        return targets

    def successors(self) -> set[str]:
        return self.linear_targets() | self.branch_targets()

    def leads_to_end(self) -> bool:
        """该节点是否有一条直接指向 END 的出边。"""

        if self.type is WorkflowNodeType.BRANCH:
            values = list(self.routes.values())
            if self.default is not None:
                values.append(self.default)
            return any(value == END_SENTINEL for value in values)
        return self.next is None or self.next == END_SENTINEL

    def subflow_spec(self) -> WorkflowGraphSpec:
        graph = self.config.get("graph")
        return parse_workflow_graph(graph)


@dataclass(frozen=True, slots=True)
class WorkflowGraphSpec:
    entrypoint: str
    nodes: tuple[WorkflowNode, ...]

    def node(self, name: str) -> WorkflowNode:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(name)

    def node_names(self) -> set[str]:
        return {node.name for node in self.nodes}

    def to_json_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, object] = {
            "entrypoint": self.entrypoint,
            "nodes": [
                {
                    "name": node.name,
                    "type": node.type.value,
                    "config": node.config,
                    "retry": (
                        {"max_attempts": node.retry.max_attempts}
                        if node.retry is not None
                        else None
                    ),
                    "next": node.next,
                    "routes": node.routes,
                    "default": node.default,
                }
                for node in self.nodes
            ],
        }
        return TypeAdapter(dict[str, JsonValue]).validate_python(payload)


def parse_workflow_graph(
    payload: object,
    *,
    _depth: int = 0,
    _allow_human_approval: bool = True,
) -> WorkflowGraphSpec:
    if _depth > MAX_SUBFLOW_DEPTH:
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(path=(), message="子流程嵌套层级超过上限")
        )
    try:
        model = WorkflowGraphModel.model_validate(payload)
    except ValidationError as error:
        first = error.errors()[0]
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(
                path=tuple(str(part) for part in first.get("loc", ())),
                message=str(first.get("msg", "工作流图定义无效")),
            )
        ) from error

    names = [node.name for node in model.nodes]
    if len(names) != len(set(names)):
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(path=("nodes",), message="节点名称必须唯一")
        )
    name_set = set(names)
    if model.entrypoint not in name_set:
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(path=("entrypoint",), message="入口节点不存在")
        )

    nodes = tuple(
        _build_node(
            node,
            name_set,
            _depth=_depth,
            _allow_human_approval=_allow_human_approval,
        )
        for node in model.nodes
    )
    spec = WorkflowGraphSpec(entrypoint=model.entrypoint, nodes=nodes)
    _validate_reachability_and_acyclicity(spec)
    return spec


def _build_node(
    model: WorkflowNodeModel,
    name_set: set[str],
    *,
    _depth: int,
    _allow_human_approval: bool,
) -> WorkflowNode:
    node = WorkflowNode(
        name=model.name,
        type=model.type,
        config=model.config,
        retry=model.retry,
        next=model.next,
        routes=model.routes,
        default=model.default,
    )
    _validate_node_shape(node, name_set, _depth=_depth, _allow_human_approval=_allow_human_approval)
    return node


def _valid_target(target: str, name_set: set[str]) -> bool:
    return target == END_SENTINEL or target in name_set


def _validate_node_shape(
    node: WorkflowNode,
    name_set: set[str],
    *,
    _depth: int,
    _allow_human_approval: bool,
) -> None:
    if node.type is WorkflowNodeType.HUMAN_APPROVAL and not _allow_human_approval:
        # 子流程通过 ainvoke 一次跑完，无法安全暂停 Interrupt；静态期即拒绝，
        # 避免产出「可注册/可发布但运行期编译才失败」的员工（与编译期约束一致）。
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(
                path=("nodes", node.name, "type"),
                message="子流程内不支持人工审批节点",
            )
        )
    if node.type is WorkflowNodeType.BRANCH:
        if node.next is not None:
            raise InvalidWorkflowGraph(
                WorkflowGraphIssue(
                    path=("nodes", node.name, "next"),
                    message="分支节点不能配置线性 next，只能使用 routes/default",
                )
            )
        state_key = node.config.get("state_key")
        if not isinstance(state_key, str) or not state_key:
            raise InvalidWorkflowGraph(
                WorkflowGraphIssue(
                    path=("nodes", node.name, "config", "state_key"),
                    message="分支节点必须声明用于路由的 state_key",
                )
            )
        all_targets = list(node.routes.values())
        if node.default is not None:
            all_targets.append(node.default)
        if not all_targets:
            raise InvalidWorkflowGraph(
                WorkflowGraphIssue(
                    path=("nodes", node.name, "routes"),
                    message="分支节点至少要有一个 route 或 default",
                )
            )
        for target in all_targets:
            if not _valid_target(target, name_set):
                raise InvalidWorkflowGraph(
                    WorkflowGraphIssue(
                        path=("nodes", node.name, "routes"),
                        message=f"分支目标 {target} 不存在",
                    )
                )
        return

    if node.routes or node.default is not None:
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(
                path=("nodes", node.name, "routes"),
                message="非分支节点不能配置 routes/default",
            )
        )
    if node.next is not None and not _valid_target(node.next, name_set):
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(
                path=("nodes", node.name, "next"),
                message=f"后继节点 {node.next} 不存在",
            )
        )
    if node.type is WorkflowNodeType.SUBFLOW:
        graph = node.config.get("graph")
        if graph is None:
            raise InvalidWorkflowGraph(
                WorkflowGraphIssue(
                    path=("nodes", node.name, "config", "graph"),
                    message="子流程节点必须内嵌 graph 定义",
                )
            )
        # 递归校验内嵌子流程（含无环、可达、深度上限）；子流程内禁用人工审批节点。
        parse_workflow_graph(graph, _depth=_depth + 1, _allow_human_approval=False)


def _validate_reachability_and_acyclicity(spec: WorkflowGraphSpec) -> None:
    by_name = {node.name: node for node in spec.nodes}

    # 可达性：从入口 BFS 覆盖全部节点。
    reachable: set[str] = set()
    frontier = [spec.entrypoint]
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        for successor in by_name[current].successors():
            if successor not in reachable:
                frontier.append(successor)
    unreachable = spec.node_names() - reachable
    if unreachable:
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(
                path=("nodes",),
                message=f"存在不可达节点：{sorted(unreachable)}",
            )
        )

    # 无环：DFS 检测回边。
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(by_name, WHITE)

    def visit(name: str) -> None:
        color[name] = GREY
        for successor in by_name[name].successors():
            if color[successor] == GREY:
                raise InvalidWorkflowGraph(
                    WorkflowGraphIssue(
                        path=("nodes",),
                        message=f"工作流图存在环：{name} -> {successor}",
                    )
                )
            if color[successor] == WHITE:
                visit(successor)
        color[name] = BLACK

    visit(spec.entrypoint)

    # 至少存在一条通向 END 的出边（否则整图无终态）。
    if not any(node.leads_to_end() for node in spec.nodes):
        raise InvalidWorkflowGraph(
            WorkflowGraphIssue(
                path=("nodes",),
                message="工作流图没有任何通向终态的路径",
            )
        )
