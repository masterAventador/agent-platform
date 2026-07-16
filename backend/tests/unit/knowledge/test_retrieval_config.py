import pytest

from agent_platform.platform.knowledge.retrieval import (
    InvalidKnowledgeRetrievalConfig,
    KnowledgeMetadataCondition,
    KnowledgeMetadataFilterCondition,
    KnowledgeRetrievalConfig,
    validate_knowledge_retrieval_config,
)


def test_empty_config_resolves_to_current_platform_defaults() -> None:
    config = validate_knowledge_retrieval_config({})

    assert config == KnowledgeRetrievalConfig()
    assert config.page_size == 5
    assert config.similarity_threshold == 0.2
    assert config.vector_similarity_weight == 0.3
    assert config.top_k == 1024
    assert config.keyword is False
    assert config.rerank_id is None
    assert config.metadata_condition is None


def test_full_config_matches_ragflow_v0_25_6_retrieval_fields() -> None:
    config = validate_knowledge_retrieval_config(
        {
            "page_size": 8,
            "similarity_threshold": 0.35,
            "vector_similarity_weight": 0.7,
            "top_k": 256,
            "keyword": True,
            "rerank_id": "BAAI/bge-reranker-v2-m3",
            "metadata_condition": {
                "logic": "or",
                "conditions": [
                    {"name": "department", "comparison_operator": "=", "value": "HR"},
                    {"name": "url", "comparison_operator": "not contains", "value": "amd"},
                ],
            },
        }
    )

    assert config.page_size == 8
    assert config.rerank_id == "BAAI/bge-reranker-v2-m3"
    assert config.metadata_condition == KnowledgeMetadataCondition(
        logic="or",
        conditions=[
            KnowledgeMetadataFilterCondition(
                name="department", comparison_operator="=", value="HR"
            ),
            KnowledgeMetadataFilterCondition(
                name="url", comparison_operator="not contains", value="amd"
            ),
        ],
    )


def test_snapshot_round_trip_preserves_explicit_values() -> None:
    config = validate_knowledge_retrieval_config({"page_size": 9, "keyword": True})

    snapshot = config.model_dump(mode="json")

    assert snapshot["page_size"] == 9
    assert snapshot["similarity_threshold"] == 0.2
    assert validate_knowledge_retrieval_config(snapshot) == config


def test_empty_style_operators_do_not_require_a_value() -> None:
    config = validate_knowledge_retrieval_config(
        {
            "metadata_condition": {
                "conditions": [{"name": "reviewed", "comparison_operator": "not empty"}],
            },
        }
    )

    assert config.metadata_condition is not None
    assert config.metadata_condition.logic == "and"
    assert config.metadata_condition.conditions[0].value == ""


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-mapping",
        ["not", "a", "mapping"],
        {"page_size": 0},
        {"page_size": 31},
        {"top_k": 0},
        {"similarity_threshold": 1.5},
        {"vector_similarity_weight": -0.1},
        {"keyword": "yes"},
        {"rerank_id": "   "},
        {"unknown_field": 1},
        {"metadata_condition": {"logic": "xor", "conditions": []}},
        {"metadata_condition": {"conditions": []}},
        {"metadata_condition": {"conditions": [{"name": "", "comparison_operator": "="}]}},
        {
            "metadata_condition": {
                "conditions": [{"name": "a", "comparison_operator": "matches", "value": "x"}],
            }
        },
        {
            "metadata_condition": {
                "conditions": [{"name": "a", "comparison_operator": "=", "value": ""}],
            }
        },
        {
            "metadata_condition": {
                "conditions": [{"name": "a", "comparison_operator": "=", "value": 3}],
            }
        },
    ],
)
def test_invalid_configurations_are_rejected_fail_closed(payload: object) -> None:
    with pytest.raises(InvalidKnowledgeRetrievalConfig):
        validate_knowledge_retrieval_config(payload)


def test_rejection_carries_a_stable_issue_path_for_api_errors() -> None:
    with pytest.raises(InvalidKnowledgeRetrievalConfig) as exception:
        validate_knowledge_retrieval_config({"page_size": 999})

    issue = exception.value.issue
    assert issue.path == ("page_size",)
    assert issue.message
