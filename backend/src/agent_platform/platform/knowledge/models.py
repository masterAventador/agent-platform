from pydantic import BaseModel, ConfigDict, Field, JsonValue


class KnowledgeDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    name: str
    document_count: int = 0
    chunk_count: int = 0


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    name: str
    status: str
    size_bytes: int = 0
    chunk_count: int = 0


class KnowledgeCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str
    document_id: str
    document_name: str
    dataset_id: str
    content: str
    score: float
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    citations: list[KnowledgeCitation]
