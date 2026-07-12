from datetime import datetime
from mimetypes import guess_type
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, File, Header, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.skills import SqlAlchemySkillRepository
from agent_platform.platform.skills.bundle import MAX_ARCHIVE_BYTES, SkillBundleError
from agent_platform.platform.skills.entities import Skill, SkillVersion
from agent_platform.platform.skills.errors import (
    SkillNameAlreadyExists,
    SkillNameMismatch,
    SkillNotFound,
    SkillVersionNotFound,
)
from agent_platform.platform.skills.ports import SkillStorage
from agent_platform.platform.skills.services import SkillService

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class SkillResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    status: str
    latest_version: int
    published_version: int | None

    @classmethod
    def from_entity(cls, skill: Skill) -> "SkillResponse":
        return cls(
            id=skill.id,
            tenant_id=skill.tenant_id,
            name=skill.name,
            description=skill.description,
            status="published" if skill.published_version is not None else "draft",
            latest_version=skill.latest_version,
            published_version=skill.published_version,
        )


class SkillVersionResponse(BaseModel):
    version: int
    description: str
    digest: str
    files: list[str]
    created_at: datetime
    published_at: datetime | None

    @classmethod
    def from_entity(cls, version: SkillVersion) -> "SkillVersionResponse":
        return cls(
            version=version.version,
            description=version.description,
            digest=version.digest,
            files=version.files,
            created_at=version.created_at,
            published_at=version.published_at,
        )


def _service(request: Request, session: AsyncSession) -> SkillService:
    return SkillService(
        repository=SqlAlchemySkillRepository(session),
        storage=cast(SkillStorage, request.app.state.skill_storage),
    )


async def _content(bundle: UploadFile) -> bytes:
    content = await bundle.read(MAX_ARCHIVE_BYTES + 1)
    if len(content) > MAX_ARCHIVE_BYTES:
        raise SkillBundleError("Skill ZIP 不能超过 10 MB")
    return content


def _raise_skill_error(error: Exception) -> None:
    if isinstance(error, (SkillNotFound, SkillVersionNotFound)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "resource_not_found", "message": "Skill 或版本不存在"},
        ) from error
    if isinstance(error, SkillNameAlreadyExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "skill_name_exists", "message": "已存在同名 Skill"},
        ) from error
    if isinstance(error, SkillNameMismatch):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "skill_name_mismatch", "message": "新版本的 Skill 名称不一致"},
        ) from error
    if isinstance(error, SkillBundleError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_skill_bundle", "message": str(error)},
        ) from error
    raise error


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(
    request: Request,
    bundle: Annotated[UploadFile, File()],
    tenant_id: TenantHeader = None,
) -> SkillResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=True
        )
        try:
            skill, _ = await _service(request, session).create(
                tenant_id=access.tenant.id,
                created_by=user.id,
                content=await _content(bundle),
            )
            await session.commit()
        except (SkillBundleError, SkillNameAlreadyExists) as error:
            _raise_skill_error(error)
            raise AssertionError("unreachable") from error
    return SkillResponse.from_entity(skill)


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    request: Request, tenant_id: TenantHeader = None
) -> list[SkillResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=False
        )
        skills = await _service(request, session).list_all(tenant_id=access.tenant.id)
    return [SkillResponse.from_entity(skill) for skill in skills]


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> SkillResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=False
        )
        try:
            skill = await _service(request, session).required_skill(
                tenant_id=access.tenant.id, skill_id=skill_id
            )
        except SkillNotFound as error:
            _raise_skill_error(error)
            raise AssertionError("unreachable") from error
    return SkillResponse.from_entity(skill)


@router.post(
    "/{skill_id}/versions",
    response_model=SkillVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_skill_version(
    skill_id: UUID,
    request: Request,
    bundle: Annotated[UploadFile, File()],
    tenant_id: TenantHeader = None,
) -> SkillVersionResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=True
        )
        try:
            _, version = await _service(request, session).add_version(
                tenant_id=access.tenant.id,
                skill_id=skill_id,
                created_by=user.id,
                content=await _content(bundle),
            )
            await session.commit()
        except (SkillBundleError, SkillNameMismatch, SkillNotFound) as error:
            _raise_skill_error(error)
            raise AssertionError("unreachable") from error
    return SkillVersionResponse.from_entity(version)


@router.get("/{skill_id}/versions", response_model=list[SkillVersionResponse])
async def list_skill_versions(
    skill_id: UUID, request: Request, tenant_id: TenantHeader = None
) -> list[SkillVersionResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=False
        )
        try:
            versions = await _service(request, session).list_versions(
                tenant_id=access.tenant.id, skill_id=skill_id
            )
        except SkillNotFound as error:
            _raise_skill_error(error)
            raise AssertionError("unreachable") from error
    return [SkillVersionResponse.from_entity(version) for version in versions]


@router.post("/{skill_id}/versions/{version}/publish", response_model=SkillResponse)
async def publish_skill_version(
    skill_id: UUID,
    version: int,
    request: Request,
    tenant_id: TenantHeader = None,
) -> SkillResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=True
        )
        try:
            skill = await _service(request, session).publish(
                tenant_id=access.tenant.id,
                skill_id=skill_id,
                version_number=version,
            )
            await session.commit()
        except (SkillNotFound, SkillVersionNotFound) as error:
            _raise_skill_error(error)
            raise AssertionError("unreachable") from error
    return SkillResponse.from_entity(skill)


@router.get("/{skill_id}/versions/{version}/files/{path:path}")
async def read_skill_file(
    skill_id: UUID,
    version: int,
    path: str,
    request: Request,
    tenant_id: TenantHeader = None,
) -> Response:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request, database_session=session, tenant_id=tenant_id, owner_required=False
        )
        try:
            content = await _service(request, session).read_file(
                tenant_id=access.tenant.id,
                skill_id=skill_id,
                version_number=version,
                path=path,
            )
        except (SkillBundleError, SkillNotFound, SkillVersionNotFound) as error:
            _raise_skill_error(error)
            raise AssertionError("unreachable") from error
    return Response(content=content, media_type=guess_type(path)[0] or "application/octet-stream")
