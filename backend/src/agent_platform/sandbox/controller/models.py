from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSandboxRequest(StrictModel):
    lease_id: UUID
    sandbox_epoch: int = Field(ge=1)


class SandboxResponse(StrictModel):
    sandbox_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class SandboxDiscoveryResponse(StrictModel):
    sandbox_ids: list[str] = Field(max_length=1)


class SandboxDeletionResponse(StrictModel):
    sandbox_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ExecRequest(StrictModel):
    command: str = Field(min_length=1, max_length=32_768)
    timeout: int | None = Field(default=None, gt=0)


class ExecResponse(StrictModel):
    output: str
    exit_code: int | None
    truncated: bool


class UploadFile(StrictModel):
    path: str
    content_base64: str = Field(max_length=22_369_624)


class UploadRequest(StrictModel):
    files: list[UploadFile] = Field(min_length=1, max_length=100)


class FileResult(StrictModel):
    path: str
    content_base64: str | None = None
    error: str | None = None


class FilesResponse(StrictModel):
    files: list[FileResult]


class DownloadRequest(StrictModel):
    paths: list[str] = Field(min_length=1, max_length=100)
