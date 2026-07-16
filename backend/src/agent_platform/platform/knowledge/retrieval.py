"""数字员工知识检索配置。

字段名与取值语义对齐 RAGFlow v0.25.6 官方检索 API（POST /api/v1/retrieval），
作为员工定义、发布快照、API 契约和 Worker 检索的单一配置来源。
"""

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

KnowledgeMetadataComparisonOperator = Literal[
    "contains",
    "not contains",
    "start with",
    "empty",
    "not empty",
    "=",
    "≠",
    ">",
    "<",
    "≥",
    "≤",
]

_VALUELESS_OPERATORS: frozenset[str] = frozenset({"empty", "not empty"})


class KnowledgeMetadataFilterCondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    comparison_operator: KnowledgeMetadataComparisonOperator
    value: Annotated[str, StringConstraints(max_length=1000)] = ""

    @model_validator(mode="after")
    def _require_value_for_comparisons(self) -> Self:
        if self.comparison_operator not in _VALUELESS_OPERATORS and not self.value:
            raise ValueError("该比较运算符必须提供 value")
        return self


class KnowledgeMetadataCondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    logic: Literal["and", "or"] = "and"
    conditions: list[KnowledgeMetadataFilterCondition] = Field(min_length=1, max_length=20)


class KnowledgeRetrievalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    page_size: int = Field(default=5, ge=1, le=30)
    similarity_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    vector_similarity_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    top_k: int = Field(default=1024, ge=1, le=4096)
    keyword: bool = False
    rerank_id: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None
    metadata_condition: KnowledgeMetadataCondition | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalConfigIssue:
    path: tuple[str, ...]
    message: str


class InvalidKnowledgeRetrievalConfig(ValueError):
    def __init__(self, issue: KnowledgeRetrievalConfigIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def validate_knowledge_retrieval_config(value: object) -> KnowledgeRetrievalConfig:
    """校验员工定义中的知识检索配置；无效配置受控拒绝（fail-closed）。"""
    try:
        return KnowledgeRetrievalConfig.model_validate(value)
    except ValidationError as error:
        first = error.errors()[0]
        raise InvalidKnowledgeRetrievalConfig(
            KnowledgeRetrievalConfigIssue(
                path=tuple(str(part) for part in first["loc"]),
                message=first["msg"],
            )
        ) from error
