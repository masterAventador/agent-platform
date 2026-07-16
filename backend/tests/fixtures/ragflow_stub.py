"""契约对齐的 RAGFlow v0.25.6 本地 Stub。

响应形态对齐官方 `/api/v1/retrieval` 经 key_mapping 后的真实字段：
文档名字段是 ``document_keyword``（不是 ``document_name``），
``document_metadata`` 仅在请求携带 ``include_metadata`` 时注入；
``top_k`` 非法时返回业务错误码信封。测试专用确定性场景：
文件名包含 ``parse-fail`` 的文档首次解析置为 FAIL，重试解析后 DONE。
"""

from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile

app = FastAPI()
datasets: dict[str, dict[str, object]] = {}
documents: dict[str, list[dict[str, Any]]] = {}

DEMO_CHUNK_CONTENT = "演示制度规定：员工每年享有十天年假。"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/datasets")
async def create_dataset(payload: dict[str, object]) -> dict[str, object]:
    dataset_id = uuid4().hex
    dataset = {"id": dataset_id, "name": payload["name"], "document_count": 0}
    datasets[dataset_id] = dataset
    documents[dataset_id] = []
    return {"code": 0, "data": dataset}


@app.delete("/api/v1/datasets")
async def delete_datasets(payload: dict[str, object]) -> dict[str, int]:
    for dataset_id in payload.get("ids", []):
        datasets.pop(str(dataset_id), None)
        documents.pop(str(dataset_id), None)
    return {"code": 0}


@app.post("/api/v1/datasets/{dataset_id}/documents")
async def upload_document(
    dataset_id: str,
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    content = await file.read()
    document = {
        "id": uuid4().hex,
        "name": file.filename,
        "run": "UNSTART",
        "size": len(content),
        "chunk_count": 0,
        "content": content.decode("utf-8", errors="replace"),
    }
    documents[dataset_id].append(document)
    return {"code": 0, "data": [_public_document(document)]}


@app.post("/api/v1/datasets/{dataset_id}/chunks")
async def parse_documents(dataset_id: str, payload: dict[str, object]) -> dict[str, int]:
    requested = set(payload["document_ids"])
    for document in documents[dataset_id]:
        if document["id"] in requested:
            if "parse-fail" in str(document["name"]) and document["run"] != "FAIL":
                document["run"] = "FAIL"
                document["chunk_count"] = 0
            else:
                document["run"] = "DONE"
                document["chunk_count"] = 1
    return {"code": 0}


@app.delete("/api/v1/datasets/{dataset_id}/documents")
async def delete_documents(dataset_id: str, payload: dict[str, object]) -> dict[str, int]:
    removed = {str(document_id) for document_id in payload.get("ids", [])}
    documents[dataset_id] = [
        document for document in documents[dataset_id] if document["id"] not in removed
    ]
    return {"code": 0}


@app.get("/api/v1/datasets/{dataset_id}/documents")
async def list_documents(dataset_id: str) -> dict[str, object]:
    docs = [_public_document(document) for document in documents[dataset_id]]
    return {"code": 0, "data": {"docs": docs, "total": len(docs)}}


@app.post("/api/v1/retrieval")
async def retrieve(payload: dict[str, object]) -> dict[str, object]:
    top_k = int(payload.get("top_k", 1024))
    if top_k <= 0:
        return {"code": 102, "message": "`top_k` must be greater than 0"}
    include_metadata = bool(payload.get("include_metadata", False))
    page_size = int(payload.get("page_size", 30))
    metadata_condition = payload.get("metadata_condition")

    chunks: list[dict[str, object]] = []
    for dataset_id in [str(value) for value in payload.get("dataset_ids", [])]:
        for document in documents.get(dataset_id, []):
            if document["run"] != "DONE":
                continue
            if not _matches_metadata(document, metadata_condition):
                continue
            chunk: dict[str, object] = {
                "id": f"chunk-{document['id']}",
                "content": DEMO_CHUNK_CONTENT,
                "content_ltks": DEMO_CHUNK_CONTENT,
                "document_id": document["id"],
                "document_keyword": document["name"],
                "dataset_id": dataset_id,
                "important_keywords": [""],
                "positions": [""],
                "similarity": 0.93,
                "term_similarity": 1.0,
                "vector_similarity": 0.9,
            }
            if include_metadata:
                chunk["document_metadata"] = document.get("metadata", {})
            chunks.append(chunk)
    chunks = chunks[:page_size]
    return {"code": 0, "data": {"total": len(chunks), "chunks": chunks}}


def _public_document(document: dict[str, Any]) -> dict[str, object]:
    return {key: value for key, value in document.items() if key not in {"content", "metadata"}}


def _matches_metadata(document: dict[str, Any], condition: object) -> bool:
    """对齐真实语义：带条件但文档无匹配 metadata 时返回空结果。"""
    if not isinstance(condition, dict):
        return True
    conditions = condition.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return True
    metadata = document.get("metadata", {})
    logic = condition.get("logic", "and")
    results = [_matches_condition(metadata, item) for item in conditions if isinstance(item, dict)]
    if not results:
        return True
    return any(results) if logic == "or" else all(results)


def _matches_condition(metadata: dict[str, object], condition: dict[str, object]) -> bool:
    name = str(condition.get("name", ""))
    operator = str(condition.get("comparison_operator", "="))
    expected = str(condition.get("value", ""))
    actual = metadata.get(name)
    if operator == "empty":
        return actual in (None, "")
    if operator == "not empty":
        return actual not in (None, "")
    if actual is None:
        return False
    text = str(actual)
    if operator == "=":
        return text == expected
    if operator == "≠":
        return text != expected
    if operator == "contains":
        return expected in text
    if operator == "not contains":
        return expected not in text
    if operator == "start with":
        return text.startswith(expected)
    try:
        left, right = float(text), float(expected)
    except ValueError:
        return False
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    if operator == "≥":
        return left >= right
    if operator == "≤":
        return left <= right
    return False
