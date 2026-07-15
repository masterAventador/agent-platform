from datetime import timedelta
from pathlib import PurePosixPath
from typing import Annotated
from unicodedata import normalize
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, UploadFile, status
from fastapi import File as FastAPIFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyArtifactRepository,
    SqlAlchemyArtifactStorageOperationRepository,
    SqlAlchemyFileRepository,
    SqlAlchemyTaskAttachmentRepository,
)
from agent_platform.infrastructure.database.repositories.runs import SqlAlchemyRunRepository
from agent_platform.platform.artifacts.entities import (
    MAX_FILE_SIZE_BYTES,
    Artifact,
    File,
    InvalidArtifactInput,
)
from agent_platform.platform.artifacts.services import ArtifactService
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.tenants.memberships import WorkspaceAccess
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission
from agent_platform.platform.users.entities import User

router = APIRouter(prefix="/api/v1", tags=["artifacts"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


def content_disposition(disposition: str, filename: str) -> str:
    """Build an RFC 6266 header without allowing filename header injection."""
    normalized = normalize("NFC", filename)
    suffix = PurePosixPath(normalized).suffix
    ascii_name = "".join(
        character
        for character in normalized
        if ord(character) < 128 and (character.isalnum() or character in {" ", ".", "-", "_"})
    ).strip(" .")
    if not ascii_name or ascii_name == suffix.lstrip("."):
        ascii_name = f"download{suffix if suffix.isascii() else ''}"
    encoded = quote(normalized, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


class FileResponse(BaseModel):
    id: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str

    @classmethod
    def from_entity(cls, file: File) -> "FileResponse":
        return cls(**{field: getattr(file, field) for field in cls.model_fields})


class AttachmentResponse(BaseModel):
    id: UUID
    workspace_path: str
    file: FileResponse


class ArtifactResponse(BaseModel):
    id: UUID
    run_id: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: str

    @classmethod
    def from_entity(cls, artifact: Artifact) -> "ArtifactResponse":
        return cls(
            id=artifact.id,
            run_id=artifact.run_id,
            name=artifact.name,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            created_at=artifact.created_at.isoformat(),
        )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


async def _authorized_run(
    *, request: Request, session: AsyncSession, tenant_id: UUID | None, run_id: UUID
) -> tuple[User, WorkspaceAccess, Run]:
    user, access = await resolve_workspace(
        request=request,
        database_session=session,
        tenant_id=tenant_id,
        required_permission=TenantPermission.RUNS_EXECUTE,
    )
    run = await SqlAlchemyRunRepository(session).get(tenant_id=access.tenant.id, run_id=run_id)
    if run is None or (
        run.created_by != user.id
        and not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
    ):
        raise _not_found()
    return user, access, run


@router.post("/files", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: Annotated[UploadFile, FastAPIFile()],
    tenant_id: TenantHeader = None,
) -> FileResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        content = await file.read(MAX_FILE_SIZE_BYTES + 1)
        service = ArtifactService(
            file_repository=SqlAlchemyFileRepository(session),
            operation_repository=SqlAlchemyArtifactStorageOperationRepository(
                session,
                heartbeat_session_factory=request.app.state.session_factory,
            ),
            storage=request.app.state.artifact_storage,
            operation_lease_duration=timedelta(
                seconds=request.app.state.settings.artifact_storage_operation_lease_seconds
            ),
            operation_heartbeat_interval=(
                request.app.state.settings.artifact_storage_operation_heartbeat_seconds
            ),
        )
        try:
            entity = await service.upload_file(
                tenant_id=access.tenant.id,
                owner_id=user.id,
                name=file.filename or "",
                media_type=file.content_type or "application/octet-stream",
                content=content,
                commit=session.commit,
            )
        except InvalidArtifactInput as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "invalid_artifact_input", "message": str(error)},
            ) from None
    return FileResponse.from_entity(entity)


@router.get("/files/{file_id}/content")
async def download_file(
    file_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> Response:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        file = await SqlAlchemyFileRepository(session).get(
            tenant_id=access.tenant.id, file_id=file_id
        )
        if file is None or (
            file.owner_id != user.id
            and not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
        ):
            raise _not_found()
        content = await ArtifactService(
            file_repository=SqlAlchemyFileRepository(session),
            operation_repository=SqlAlchemyArtifactStorageOperationRepository(session),
            storage=request.app.state.artifact_storage,
        ).read_file(file)
    return Response(
        content=content,
        media_type=file.media_type,
        headers={"Content-Disposition": content_disposition("inline", file.name)},
    )


@router.get("/runs/{run_id}/attachments", response_model=list[AttachmentResponse])
async def list_attachments(
    run_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> list[AttachmentResponse]:
    async with request.app.state.session_factory() as session:
        _, access, _ = await _authorized_run(
            request=request, session=session, tenant_id=tenant_id, run_id=run_id
        )
        attachments = await SqlAlchemyTaskAttachmentRepository(session).list_for_run(
            tenant_id=access.tenant.id, run_id=run_id
        )
        files = SqlAlchemyFileRepository(session)
        response: list[AttachmentResponse] = []
        for attachment in attachments:
            file = await files.get(tenant_id=access.tenant.id, file_id=attachment.file_id)
            if file is None:
                continue
            response.append(
                AttachmentResponse(
                    id=attachment.id,
                    workspace_path=attachment.workspace_path,
                    file=FileResponse.from_entity(file),
                )
            )
        return response


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    run_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> list[ArtifactResponse]:
    async with request.app.state.session_factory() as session:
        _, access, _ = await _authorized_run(
            request=request, session=session, tenant_id=tenant_id, run_id=run_id
        )
        artifacts = await SqlAlchemyArtifactRepository(session).list_for_run(
            tenant_id=access.tenant.id, run_id=run_id
        )
        return [ArtifactResponse.from_entity(artifact) for artifact in artifacts]


@router.get("/artifacts/{artifact_id}/content")
async def download_artifact(
    artifact_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> Response:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        artifact = await SqlAlchemyArtifactRepository(session).get(
            tenant_id=access.tenant.id, artifact_id=artifact_id
        )
        if artifact is None:
            raise _not_found()
        run = await SqlAlchemyRunRepository(session).get(
            tenant_id=access.tenant.id, run_id=artifact.run_id
        )
        if run is None or (
            run.created_by != user.id
            and not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
        ):
            raise _not_found()
        content = await ArtifactService(
            file_repository=SqlAlchemyFileRepository(session),
            artifact_repository=SqlAlchemyArtifactRepository(session),
            operation_repository=SqlAlchemyArtifactStorageOperationRepository(session),
            storage=request.app.state.artifact_storage,
        ).read_artifact(artifact)
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": content_disposition("attachment", artifact.name)},
    )


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> Response:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        repository = SqlAlchemyArtifactRepository(session)
        artifact = await repository.get(tenant_id=access.tenant.id, artifact_id=artifact_id)
        if artifact is None:
            raise _not_found()
        run = await SqlAlchemyRunRepository(session).get(
            tenant_id=access.tenant.id, run_id=artifact.run_id
        )
        if run is None or (
            run.created_by != user.id
            and not role_has_permission(role=access.role, permission=TenantPermission.RUNS_MANAGE)
        ):
            raise _not_found()
        await ArtifactService(
            file_repository=SqlAlchemyFileRepository(session),
            artifact_repository=repository,
            operation_repository=SqlAlchemyArtifactStorageOperationRepository(
                session,
                heartbeat_session_factory=request.app.state.session_factory,
            ),
            storage=request.app.state.artifact_storage,
            operation_lease_duration=timedelta(
                seconds=request.app.state.settings.artifact_storage_operation_lease_seconds
            ),
            operation_heartbeat_interval=(
                request.app.state.settings.artifact_storage_operation_heartbeat_seconds
            ),
        ).delete_artifact(artifact, commit=session.commit)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
