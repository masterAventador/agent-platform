from typing import Protocol

from pydantic import JsonValue

from agent_platform.platform.knowledge.models import (
    KnowledgeDataset,
    KnowledgeDocument,
    KnowledgeSearchResult,
)


class KnowledgeProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def create_dataset(
        self, *, name: str, description: str = "", chunk_method: str = "naive"
    ) -> KnowledgeDataset: ...

    async def delete_dataset(self, provider_id: str) -> None: ...

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> KnowledgeDocument: ...

    async def start_parsing(self, *, dataset_id: str, document_ids: list[str]) -> None: ...

    async def delete_documents(self, *, dataset_id: str, document_ids: list[str]) -> None: ...

    async def list_documents(self, *, dataset_id: str) -> list[KnowledgeDocument]: ...

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        page_size: int = 10,
        metadata_condition: dict[str, JsonValue] | None = None,
    ) -> KnowledgeSearchResult: ...
