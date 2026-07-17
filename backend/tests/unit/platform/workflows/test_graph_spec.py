import pytest

from agent_platform.platform.workflows.graph_spec import (
    END_SENTINEL,
    InvalidWorkflowGraph,
    WorkflowGraphSpec,
    WorkflowNodeType,
    parse_workflow_graph,
)


def _linear_spec() -> dict[str, object]:
    return {
        "entrypoint": "start",
        "nodes": [
            {"name": "start", "type": "agent", "config": {"prompt": "hi"}, "next": "finish"},
            {"name": "finish", "type": "agent", "config": {"prompt": "bye"}, "next": None},
        ],
    }


def test_parse_valid_linear_spec() -> None:
    spec = parse_workflow_graph(_linear_spec())
    assert isinstance(spec, WorkflowGraphSpec)
    assert spec.entrypoint == "start"
    assert spec.node("finish").is_terminal
    assert spec.node("start").type is WorkflowNodeType.AGENT


def test_entrypoint_must_exist() -> None:
    payload = _linear_spec()
    payload["entrypoint"] = "ghost"
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_transition_target_must_exist() -> None:
    payload = _linear_spec()
    payload["nodes"][0]["next"] = "nowhere"  # type: ignore[index]
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_terminal_sentinel_is_allowed_target() -> None:
    payload = _linear_spec()
    payload["nodes"][1]["next"] = END_SENTINEL  # type: ignore[index]
    spec = parse_workflow_graph(payload)
    assert spec.node("finish").next == END_SENTINEL
    assert spec.node("finish").is_terminal


def test_duplicate_node_names_rejected() -> None:
    payload = _linear_spec()
    payload["nodes"].append(  # type: ignore[attr-defined]
        {"name": "start", "type": "agent", "config": {"prompt": "dup"}, "next": None}
    )
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_cycle_is_detected() -> None:
    payload = {
        "entrypoint": "a",
        "nodes": [
            {"name": "a", "type": "agent", "config": {}, "next": "b"},
            {"name": "b", "type": "agent", "config": {}, "next": "a"},
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_unreachable_node_is_detected() -> None:
    payload = {
        "entrypoint": "a",
        "nodes": [
            {"name": "a", "type": "agent", "config": {}, "next": None},
            {"name": "orphan", "type": "agent", "config": {}, "next": None},
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_branch_routes_must_reference_existing_nodes() -> None:
    payload = {
        "entrypoint": "router",
        "nodes": [
            {
                "name": "router",
                "type": "branch",
                "config": {"state_key": "category"},
                "routes": {"vip": "ghost"},
                "default": "fallback",
            },
            {"name": "fallback", "type": "agent", "config": {}, "next": None},
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_branch_requires_state_key_and_targets() -> None:
    payload = {
        "entrypoint": "router",
        "nodes": [
            {
                "name": "router",
                "type": "branch",
                "config": {"state_key": "category"},
                "routes": {"vip": "vip_node"},
                "default": "fallback",
            },
            {"name": "vip_node", "type": "agent", "config": {}, "next": "fallback"},
            {"name": "fallback", "type": "agent", "config": {}, "next": None},
        ],
    }
    spec = parse_workflow_graph(payload)
    router = spec.node("router")
    assert router.type is WorkflowNodeType.BRANCH
    assert router.branch_targets() == {"vip_node", "fallback"}


def test_branch_without_any_target_is_rejected() -> None:
    payload = {
        "entrypoint": "router",
        "nodes": [
            {
                "name": "router",
                "type": "branch",
                "config": {"state_key": "category"},
                "routes": {},
                "default": None,
            },
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_retry_bounds_enforced() -> None:
    payload = _linear_spec()
    payload["nodes"][0]["retry"] = {"max_attempts": 0}  # type: ignore[index]
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_retry_upper_bound_enforced() -> None:
    payload = _linear_spec()
    payload["nodes"][0]["retry"] = {"max_attempts": 99}  # type: ignore[index]
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_human_approval_and_subagent_and_subflow_parse() -> None:
    payload = {
        "entrypoint": "collect",
        "nodes": [
            {"name": "collect", "type": "agent", "config": {"prompt": "collect"}, "next": "review"},
            {
                "name": "review",
                "type": "human_approval",
                "config": {"title": "请审批"},
                "next": "delegate",
            },
            {
                "name": "delegate",
                "type": "subagent",
                "config": {"prompt": "do deep work"},
                "next": "wrap",
            },
            {
                "name": "wrap",
                "type": "subflow",
                "config": {
                    "graph": {
                        "entrypoint": "inner",
                        "nodes": [
                            {"name": "inner", "type": "agent", "config": {}, "next": None},
                        ],
                    }
                },
                "next": None,
            },
        ],
    }
    spec = parse_workflow_graph(payload)
    assert spec.node("review").type is WorkflowNodeType.HUMAN_APPROVAL
    assert spec.node("delegate").type is WorkflowNodeType.SUBAGENT
    assert spec.node("wrap").type is WorkflowNodeType.SUBFLOW
    assert spec.node("wrap").subflow_spec().entrypoint == "inner"


def test_subflow_nested_graph_is_validated() -> None:
    payload = {
        "entrypoint": "wrap",
        "nodes": [
            {
                "name": "wrap",
                "type": "subflow",
                "config": {
                    "graph": {
                        "entrypoint": "inner",
                        "nodes": [
                            {"name": "inner", "type": "agent", "config": {}, "next": "cycle"},
                            {"name": "cycle", "type": "agent", "config": {}, "next": "inner"},
                        ],
                    }
                },
                "next": None,
            },
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_subflow_with_human_approval_rejected_at_parse() -> None:
    """子流程内含人工审批节点必须在静态解析期即拒绝（与编译期 allow_interrupts=False 一致）。"""

    payload = {
        "entrypoint": "wrap",
        "nodes": [
            {
                "name": "wrap",
                "type": "subflow",
                "config": {
                    "graph": {
                        "entrypoint": "collect",
                        "nodes": [
                            {
                                "name": "collect",
                                "type": "agent",
                                "config": {},
                                "next": "review",
                            },
                            {
                                "name": "review",
                                "type": "human_approval",
                                "config": {"title": "子流程内审批"},
                                "next": None,
                            },
                        ],
                    }
                },
                "next": None,
            },
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_nested_subflow_human_approval_rejected_at_parse() -> None:
    """更深层子流程内的人工审批节点也必须被静态拒绝。"""

    payload = {
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
                                "type": "subflow",
                                "config": {
                                    "graph": {
                                        "entrypoint": "approve",
                                        "nodes": [
                                            {
                                                "name": "approve",
                                                "type": "human_approval",
                                                "config": {},
                                                "next": None,
                                            }
                                        ],
                                    }
                                },
                                "next": None,
                            }
                        ],
                    }
                },
                "next": None,
            }
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_top_level_human_approval_still_allowed() -> None:
    """顶层图仍允许人工审批节点（只有子流程内禁用）。"""

    payload = {
        "entrypoint": "review",
        "nodes": [
            {"name": "review", "type": "human_approval", "config": {}, "next": None},
        ],
    }
    spec = parse_workflow_graph(payload)
    assert spec.node("review").type is WorkflowNodeType.HUMAN_APPROVAL


def test_at_least_one_terminal_path_required() -> None:
    # entrypoint node routing only into a branch whose targets all loop back
    payload = {
        "entrypoint": "a",
        "nodes": [
            {"name": "a", "type": "agent", "config": {}, "next": "b"},
            {"name": "b", "type": "agent", "config": {}, "next": "a"},
        ],
    }
    with pytest.raises(InvalidWorkflowGraph):
        parse_workflow_graph(payload)


def test_roundtrip_to_json_dict() -> None:
    spec = parse_workflow_graph(_linear_spec())
    payload = spec.to_json_dict()
    reparsed = parse_workflow_graph(payload)
    assert reparsed.to_json_dict() == payload
