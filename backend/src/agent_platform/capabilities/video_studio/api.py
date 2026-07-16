from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.capabilities.video_studio.media_library import (
    DownloadTask,
    InvalidDownloadTaskTransition,
    InvalidMaterialInput,
    Material,
    MaterialFolder,
    MaterialFolderNotFoundError,
    MaterialInUseError,
    MaterialKind,
    MaterialNotFoundError,
    MaterialReference,
    MaterialReferenceAlreadyExistsError,
    MediaLibraryService,
    UploadCredentialExpiredError,
)
from agent_platform.capabilities.video_studio.persistence import (
    SqlAlchemyMediaLibraryRepository,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedMaterialPreview,
    IssuedUploadCredentials,
    MaterialObjectVerifier,
    MaterialPreviewUrlIssuer,
    MaterialUploadCredentialIssuer,
)
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission

router = APIRouter(prefix="/api/v1/video-studio", tags=["video-studio"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class MaterialFolderResponse(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_entity(cls, folder: MaterialFolder) -> MaterialFolderResponse:
        return cls(
            id=folder.id,
            parent_id=folder.parent_id,
            name=folder.name,
            created_by=folder.created_by,
            created_at=folder.created_at,
        )


class MaterialFolderCreateRequest(BaseModel):
    name: str
    parent_id: UUID | None = None


class MaterialFolderListResponse(BaseModel):
    items: list[MaterialFolderResponse]


class MaterialResponse(BaseModel):
    id: UUID
    folder_id: UUID | None
    name: str
    kind: str
    media_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    status: str
    tags: list[str]
    cleanup_required: bool
    artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, material: Material) -> MaterialResponse:
        return cls(
            id=material.id,
            folder_id=material.folder_id,
            name=material.name,
            kind=material.kind.value,
            media_type=material.media_type,
            size_bytes=material.size_bytes,
            sha256=material.sha256,
            storage_key=material.storage_key,
            status=material.status,
            tags=list(material.tags),
            cleanup_required=material.cleanup_required,
            artifact_id=material.artifact_id,
            created_at=material.created_at,
            updated_at=material.updated_at,
        )


class UploadCredentialsResponse(BaseModel):
    provider: str
    bucket: str
    region: str
    key_prefix: str
    tmp_secret_id: str
    tmp_secret_key: str
    session_token: str
    expires_at: datetime

    @classmethod
    def from_entity(cls, credentials: IssuedUploadCredentials) -> UploadCredentialsResponse:
        return cls(**asdict(credentials))


class MaterialUploadCredentialRequest(BaseModel):
    name: str
    kind: MaterialKind
    media_type: str
    size_bytes: int = Field(gt=0)
    sha256: str
    folder_id: UUID | None = None
    tag_names: list[str] = Field(default_factory=list, max_length=50)


class MaterialUploadCredentialResponse(BaseModel):
    material: MaterialResponse
    credentials: UploadCredentialsResponse


class MaterialListResponse(BaseModel):
    items: list[MaterialResponse]


class MaterialPreviewResponse(BaseModel):
    url: str
    expires_at: datetime

    @classmethod
    def from_entity(cls, preview: IssuedMaterialPreview) -> MaterialPreviewResponse:
        return cls(url=preview.url, expires_at=preview.expires_at)


class MaterialReferenceCreateRequest(BaseModel):
    reference_type: str
    reference_id: UUID


class MaterialReferenceResponse(BaseModel):
    id: UUID
    material_id: UUID
    reference_type: str
    reference_id: UUID
    created_at: datetime

    @classmethod
    def from_entity(cls, reference: MaterialReference) -> MaterialReferenceResponse:
        return cls(
            id=reference.id,
            material_id=reference.material_id,
            reference_type=reference.reference_type,
            reference_id=reference.reference_id,
            created_at=reference.created_at,
        )


class MaterialReferenceListResponse(BaseModel):
    items: list[MaterialReferenceResponse]


class DownloadTaskCreateRequest(BaseModel):
    source_type: str
    source_id: UUID


class DownloadTaskFailRequest(BaseModel):
    downloaded_bytes: int = Field(ge=0)
    resume_token: str | None = Field(default=None, max_length=500)
    error_code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    retryable: bool = True


class DownloadTaskResponse(BaseModel):
    id: UUID
    source_type: str
    source_id: UUID
    status: str
    progress: int
    downloaded_bytes: int
    total_bytes: int
    resume_token: str | None
    error_code: str | None
    retryable: bool
    retry_count: int
    revision: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_entity(cls, task: DownloadTask) -> DownloadTaskResponse:
        return cls(
            id=task.id,
            source_type=task.source_type,
            source_id=task.source_id,
            status=task.status.value,
            progress=task.progress,
            downloaded_bytes=task.downloaded_bytes,
            total_bytes=task.total_bytes,
            resume_token=task.resume_token,
            error_code=task.error_code,
            retryable=task.retryable,
            retry_count=task.retry_count,
            revision=task.revision,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
        )


class DownloadTaskListResponse(BaseModel):
    items: list[DownloadTaskResponse]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


def _invalid(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "invalid_video_material_input", "message": message},
    )


def _unavailable(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": code, "message": message},
    )


def _credential_issuer(request: Request) -> MaterialUploadCredentialIssuer:
    configured = getattr(request.app.state, "video_material_upload_credential_issuer", None)
    if configured is None:
        raise _unavailable(
            "video_material_sts_not_configured",
            "素材上传临时凭证服务未配置",
        )
    return cast(MaterialUploadCredentialIssuer, configured)


def _object_verifier(request: Request) -> MaterialObjectVerifier:
    configured = getattr(request.app.state, "video_material_object_verifier", None)
    if configured is None:
        raise _unavailable(
            "video_material_storage_not_configured",
            "素材对象存储校验服务未配置",
        )
    return cast(MaterialObjectVerifier, configured)


def _preview_issuer(request: Request) -> MaterialPreviewUrlIssuer:
    configured = getattr(request.app.state, "video_material_preview_url_issuer", None)
    if configured is None:
        raise _unavailable(
            "video_material_preview_not_configured",
            "素材预览服务未配置",
        )
    return cast(MaterialPreviewUrlIssuer, configured)


def _service(
    request: Request,
    repository: SqlAlchemyMediaLibraryRepository,
    *,
    require_preview: bool = False,
) -> MediaLibraryService:
    return MediaLibraryService(
        repository=repository,
        credential_issuer=_credential_issuer(request),
        object_verifier=_object_verifier(request),
        preview_issuer=_preview_issuer(request) if require_preview else None,
    )


@router.post(
    "/material-folders",
    response_model=MaterialFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_material_folder(
    payload: MaterialFolderCreateRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialFolderResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        try:
            folder = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).create_folder(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                name=payload.name,
                parent_id=payload.parent_id,
            )
        except MaterialFolderNotFoundError:
            raise _not_found() from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return MaterialFolderResponse.from_entity(folder)


@router.get("/material-folders", response_model=MaterialFolderListResponse)
async def list_material_folders(
    request: Request,
    parent_id: UUID | None = None,
    tenant_id: TenantHeader = None,
) -> MaterialFolderListResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            folders = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).list_folders(tenant_id=access.tenant.id, parent_id=parent_id)
        except MaterialFolderNotFoundError:
            raise _not_found() from None
        return MaterialFolderListResponse(
            items=[MaterialFolderResponse.from_entity(folder) for folder in folders],
        )


@router.post(
    "/materials/upload-credentials",
    response_model=MaterialUploadCredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_material_upload_credentials(
    payload: MaterialUploadCredentialRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialUploadCredentialResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        repository = SqlAlchemyMediaLibraryRepository(session)
        try:
            draft = await _service(request, repository).request_upload_credentials(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                name=payload.name,
                kind=payload.kind,
                media_type=payload.media_type,
                size_bytes=payload.size_bytes,
                sha256=payload.sha256,
                tag_names=tuple(payload.tag_names),
                folder_id=payload.folder_id,
            )
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        except MaterialFolderNotFoundError:
            raise _not_found() from None
        await session.commit()
        return MaterialUploadCredentialResponse(
            material=MaterialResponse.from_entity(draft.material),
            credentials=UploadCredentialsResponse.from_entity(draft.credentials),
        )


@router.post("/materials/{material_id}/complete-upload", response_model=MaterialResponse)
async def complete_material_upload(
    material_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        repository = SqlAlchemyMediaLibraryRepository(session)
        try:
            material = await _service(request, repository).complete_upload(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                material_id=material_id,
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except UploadCredentialExpiredError as error:
            await session.commit()
            raise _conflict("upload_credential_expired", str(error)) from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return MaterialResponse.from_entity(material)


@router.post("/materials/{material_id}/abort-upload", response_model=MaterialResponse)
async def abort_material_upload(
    material_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        try:
            material = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).abort_upload(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                material_id=material_id,
                manage_all=role_has_permission(
                    role=access.role,
                    permission=TenantPermission.RUNS_MANAGE,
                ),
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return MaterialResponse.from_entity(material)


@router.get("/materials", response_model=MaterialListResponse)
async def list_materials(
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialListResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        materials = await _service(
            request,
            SqlAlchemyMediaLibraryRepository(session),
        ).list_materials(tenant_id=access.tenant.id)
        return MaterialListResponse(
            items=[MaterialResponse.from_entity(item) for item in materials],
        )


@router.get("/materials/{material_id}", response_model=MaterialResponse)
async def get_material(
    material_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            material = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).get_material(tenant_id=access.tenant.id, material_id=material_id)
        except MaterialNotFoundError:
            raise _not_found() from None
        return MaterialResponse.from_entity(material)


@router.get("/materials/{material_id}/preview", response_model=MaterialPreviewResponse)
async def preview_material(
    material_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialPreviewResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            preview = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
                require_preview=True,
            ).request_preview_url(
                tenant_id=access.tenant.id,
                material_id=material_id,
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        return MaterialPreviewResponse.from_entity(preview)


@router.post(
    "/materials/{material_id}/references",
    response_model=MaterialReferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_material_reference(
    material_id: UUID,
    payload: MaterialReferenceCreateRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialReferenceResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        try:
            reference = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).add_reference(
                tenant_id=access.tenant.id,
                material_id=material_id,
                reference_type=payload.reference_type,
                reference_id=payload.reference_id,
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except MaterialReferenceAlreadyExistsError as error:
            raise _conflict("reference_already_exists", str(error)) from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return MaterialReferenceResponse.from_entity(reference)


@router.get(
    "/materials/{material_id}/references",
    response_model=MaterialReferenceListResponse,
)
async def list_material_references(
    material_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MaterialReferenceListResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            references = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).list_references(tenant_id=access.tenant.id, material_id=material_id)
        except MaterialNotFoundError:
            raise _not_found() from None
        return MaterialReferenceListResponse(
            items=[MaterialReferenceResponse.from_entity(item) for item in references],
        )


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> None:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        try:
            await _service(request, SqlAlchemyMediaLibraryRepository(session)).delete_material(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                material_id=material_id,
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except MaterialInUseError as error:
            raise _conflict("material_in_use", str(error)) from None
        await session.commit()


@router.post(
    "/download-tasks",
    response_model=DownloadTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_download_task(
    payload: DownloadTaskCreateRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            task = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).create_download_task(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                source_type=payload.source_type,
                source_id=payload.source_id,
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return DownloadTaskResponse.from_entity(task)


@router.get("/download-tasks", response_model=DownloadTaskListResponse)
async def list_download_tasks(
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskListResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        tasks = await _service(
            request,
            SqlAlchemyMediaLibraryRepository(session),
        ).list_download_tasks(tenant_id=access.tenant.id)
        return DownloadTaskListResponse(
            items=[DownloadTaskResponse.from_entity(task) for task in tasks],
        )


@router.post("/download-tasks/{task_id}/start", response_model=DownloadTaskResponse)
async def start_download_task(
    task_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        try:
            task = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).start_download_task(tenant_id=access.tenant.id, task_id=task_id)
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidDownloadTaskTransition as error:
            raise _conflict("invalid_download_task_transition", str(error)) from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return DownloadTaskResponse.from_entity(task)


@router.post("/download-tasks/{task_id}/fail", response_model=DownloadTaskResponse)
async def fail_download_task(
    task_id: UUID,
    payload: DownloadTaskFailRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        service = _service(request, SqlAlchemyMediaLibraryRepository(session))
        try:
            await service.update_download_progress(
                tenant_id=access.tenant.id,
                task_id=task_id,
                downloaded_bytes=payload.downloaded_bytes,
                resume_token=payload.resume_token,
            )
            task = await service.fail_download_task(
                tenant_id=access.tenant.id,
                task_id=task_id,
                error_code=payload.error_code,
                retryable=payload.retryable,
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidDownloadTaskTransition as error:
            raise _conflict("invalid_download_task_transition", str(error)) from None
        except InvalidMaterialInput as error:
            raise _invalid(str(error)) from None
        await session.commit()
        return DownloadTaskResponse.from_entity(task)


@router.post("/download-tasks/{task_id}/retry", response_model=DownloadTaskResponse)
async def retry_download_task(
    task_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            task = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).retry_download_task(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                task_id=task_id,
                manage_all=role_has_permission(
                    role=access.role,
                    permission=TenantPermission.RUNS_MANAGE,
                ),
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidDownloadTaskTransition as error:
            raise _conflict("invalid_download_task_transition", str(error)) from None
        await session.commit()
        return DownloadTaskResponse.from_entity(task)


@router.post("/download-tasks/{task_id}/complete", response_model=DownloadTaskResponse)
async def complete_download_task(
    task_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_MANAGE,
        )
        try:
            task = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).complete_download_task(tenant_id=access.tenant.id, task_id=task_id)
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidDownloadTaskTransition as error:
            raise _conflict("invalid_download_task_transition", str(error)) from None
        await session.commit()
        return DownloadTaskResponse.from_entity(task)


@router.post("/download-tasks/{task_id}/cancel", response_model=DownloadTaskResponse)
async def cancel_download_task(
    task_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> DownloadTaskResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        try:
            task = await _service(
                request,
                SqlAlchemyMediaLibraryRepository(session),
            ).cancel_download_task(
                tenant_id=access.tenant.id,
                actor_id=user.id,
                task_id=task_id,
                manage_all=role_has_permission(
                    role=access.role,
                    permission=TenantPermission.RUNS_MANAGE,
                ),
            )
        except MaterialNotFoundError:
            raise _not_found() from None
        except InvalidDownloadTaskTransition as error:
            raise _conflict("invalid_download_task_transition", str(error)) from None
        await session.commit()
        return DownloadTaskResponse.from_entity(task)
