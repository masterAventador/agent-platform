from pathlib import Path
from typing import Any

import httpx
from pydantic import JsonValue, TypeAdapter

from agent_platform.platform.knowledge.models import (
    KnowledgeCitation,
    KnowledgeDataset,
    KnowledgeDocument,
    KnowledgeSearchResult,
)


class RagFlowError(Exception):
    """RAGFlow 官方 API 调用失败。"""


class RagFlowClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        chunk_method: str = "naive",
    ) -> KnowledgeDataset:
        data = await self._request(
            "POST",
            "/api/v1/datasets",
            json={
                "name": name,
                "description": description,
                "permission": "me",
                "chunk_method": chunk_method,
            },
        )
        return self._dataset(data)

    async def delete_dataset(self, provider_id: str) -> None:
        await self._request("DELETE", "/api/v1/datasets", json={"ids": [provider_id]})

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> KnowledgeDocument:
        data = await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/documents",
            files={"file": (Path(filename).name, content, content_type)},
        )
        if not isinstance(data, list) or not data:
            raise RagFlowError("RAGFlow 未返回上传文档")
        return self._document(data[0])

    async def start_parsing(self, *, dataset_id: str, document_ids: list[str]) -> None:
        await self._request(
            "POST",
            f"/api/v1/datasets/{dataset_id}/chunks",
            json={"document_ids": document_ids},
        )

    async def list_documents(self, *, dataset_id: str) -> list[KnowledgeDocument]:
        data = await self._request(
            "GET",
            f"/api/v1/datasets/{dataset_id}/documents",
            params={"page": 1, "page_size": 100, "orderby": "create_time", "desc": "true"},
        )
        records = data.get("docs", []) if isinstance(data, dict) else []
        return [self._document(record) for record in records]

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        page_size: int = 10,
        metadata_condition: dict[str, JsonValue] | None = None,
    ) -> KnowledgeSearchResult:
        payload: dict[str, Any] = {
            "question": question,
            "dataset_ids": dataset_ids,
            "page": 1,
            "page_size": page_size,
        }
        if metadata_condition is not None:
            payload["metadata_condition"] = metadata_condition
        data = await self._request("POST", "/api/v1/retrieval", json=payload)
        if not isinstance(data, dict):
            raise RagFlowError("RAGFlow 检索响应格式错误")
        chunks = data.get("chunks", [])
        return KnowledgeSearchResult(
            total=int(data.get("total", len(chunks))),
            citations=[self._citation(chunk) for chunk in chunks],
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, headers=self._headers, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise RagFlowError("RAGFlow 服务暂时不可用") from error
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("code") != 0:
            message = (
                envelope.get("message", "未知错误")
                if isinstance(envelope, dict)
                else "响应格式错误"
            )
            raise RagFlowError(str(message))
        return envelope.get("data")

    @staticmethod
    def _dataset(data: Any) -> KnowledgeDataset:
        if not isinstance(data, dict):
            raise RagFlowError("RAGFlow 数据集响应格式错误")
        return KnowledgeDataset(
            provider_id=str(data["id"]),
            name=str(data["name"]),
            document_count=int(data.get("document_count", 0)),
            chunk_count=int(data.get("chunk_count", 0)),
        )

    @staticmethod
    def _document(data: Any) -> KnowledgeDocument:
        if not isinstance(data, dict):
            raise RagFlowError("RAGFlow 文档响应格式错误")
        return KnowledgeDocument(
            provider_id=str(data["id"]),
            name=str(data["name"]),
            status=str(data.get("run", "UNSTART")),
            size_bytes=int(data.get("size", 0)),
            chunk_count=int(data.get("chunk_count", 0)),
        )

    @staticmethod
    def _citation(data: Any) -> KnowledgeCitation:
        if not isinstance(data, dict):
            raise RagFlowError("RAGFlow 切片响应格式错误")
        metadata = TypeAdapter(dict[str, JsonValue]).validate_python(
            data.get("document_metadata", {})
        )
        return KnowledgeCitation(
            chunk_id=str(data["id"]),
            document_id=str(data["document_id"]),
            document_name=str(data.get("document_name", "")),
            dataset_id=str(data["dataset_id"]),
            content=str(data.get("content", "")),
            score=float(data.get("similarity", 0.0)),
            metadata=metadata,
        )
