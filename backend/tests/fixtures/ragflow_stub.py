from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile

app = FastAPI()
datasets: dict[str, dict[str, object]] = {}
documents: dict[str, list[dict[str, object]]] = {}


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
    }
    documents[dataset_id].append(document)
    return {"code": 0, "data": [document]}


@app.post("/api/v1/datasets/{dataset_id}/chunks")
async def parse_documents(dataset_id: str, payload: dict[str, object]) -> dict[str, int]:
    requested = set(payload["document_ids"])
    for document in documents[dataset_id]:
        if document["id"] in requested:
            document["run"] = "DONE"
            document["chunk_count"] = 1
    return {"code": 0}


@app.get("/api/v1/datasets/{dataset_id}/documents")
async def list_documents(dataset_id: str) -> dict[str, object]:
    return {"code": 0, "data": {"docs": documents[dataset_id], "total": len(documents[dataset_id])}}


@app.post("/api/v1/retrieval")
async def retrieve(payload: dict[str, object]) -> dict[str, object]:
    dataset_id = str(payload["dataset_ids"][0])
    document = documents[dataset_id][0]
    return {
        "code": 0,
        "data": {
            "total": 1,
            "chunks": [{
                "id": "chunk-demo",
                "document_id": document["id"],
                "document_name": document["name"],
                "dataset_id": dataset_id,
                "content": "演示制度规定：员工每年享有十天年假。",
                "similarity": 0.93,
                "document_metadata": {},
            }],
        },
    }
