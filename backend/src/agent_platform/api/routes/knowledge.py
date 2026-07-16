from hashlib import sha256
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, JsonValue, StringConstraints
from sqlalchemy.exc import IntegrityError

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.knowledge import (
    SqlAlchemyKnowledgeBaseRepository,
)
from agent_platform.platform.knowledge.entities import KnowledgeBase
from agent_platform.platform.knowledge.models import (
    KnowledgeDocument,
    KnowledgeSearchResult,
)
from agent_platform.platform.knowledge.ports import KnowledgeProvider
from agent_platform.platform.knowledge.registry import KnowledgeProviderRegistry
from agent_platform.platform.tenants.permissions import TenantPermission

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class CreateKnowledgeBaseRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: str = Field(default="", max_length=4000)


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str
    provider: str

    @classmethod
    def from_entity(cls, value: KnowledgeBase) -> "KnowledgeBaseResponse":
        return cls(
            id=value.id,
            tenant_id=value.tenant_id,
            name=value.name,
            description=value.description,
            provider=value.provider,
        )


class RetrieveRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    limit: int = Field(default=10, ge=1, le=30)
    metadata_condition: dict[str, JsonValue] | None = None


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "知识库不存在"},
    )


def _providers(request: Request) -> KnowledgeProviderRegistry:
    return cast(KnowledgeProviderRegistry, request.app.state.knowledge_provider_registry)


def _provider_for(request: Request, knowledge_base: KnowledgeBase) -> KnowledgeProvider:
    return _providers(request).resolve(knowledge_base.provider)


def _provider_dataset_name(tenant_id: UUID, name: str) -> str:
    digest = sha256(name.casefold().encode()).hexdigest()[:10]
    return f"{tenant_id.hex[:8]}-{name[:90]}-{digest}"


async def _get_base(request: Request, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase:
    async with request.app.state.session_factory() as session:
        value = await SqlAlchemyKnowledgeBaseRepository(session).get(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
        )
    if value is None:
        raise _not_found()
    return value


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: CreateKnowledgeBaseRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> KnowledgeBaseResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.KNOWLEDGE_MANAGE,
        )
        provider = _providers(request).default_provider
        dataset = await provider.create_dataset(
            name=_provider_dataset_name(access.tenant.id, payload.name),
            description=payload.description,
        )
        value = KnowledgeBase.create(
            tenant_id=access.tenant.id,
            name=payload.name,
            description=payload.description,
            provider=provider.provider_name,
            provider_id=dataset.provider_id,
            created_by=user.id,
        )
        try:
            await SqlAlchemyKnowledgeBaseRepository(session).add(value)
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="knowledge_base.created",
                resource_type="knowledge_base",
                resource_id=value.id,
                metadata={"provider": value.provider},
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            await provider.delete_dataset(dataset.provider_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "knowledge_base_name_exists", "message": "知识库名称已存在"},
            ) from error
    return KnowledgeBaseResponse.from_entity(value)


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    request: Request, tenant_id: TenantHeader = None
) -> list[KnowledgeBaseResponse]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=None,
        )
        values = await SqlAlchemyKnowledgeBaseRepository(session).list(tenant_id=access.tenant.id)
    return [KnowledgeBaseResponse.from_entity(value) for value in values]


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> None:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.KNOWLEDGE_MANAGE,
        )
        repository = SqlAlchemyKnowledgeBaseRepository(session)
        value = await repository.get(
            tenant_id=access.tenant.id, knowledge_base_id=knowledge_base_id
        )
        if value is None:
            raise _not_found()
        provider = _provider_for(request, value)
        await provider.delete_dataset(value.provider_id)
        await repository.delete(value)
        await emit_audit_event(
            session,
            tenant_id=access.tenant.id,
            actor_user_id=user.id,
            action="knowledge_base.deleted",
            resource_type="knowledge_base",
            resource_id=value.id,
            metadata={"provider": value.provider},
        )
        await session.commit()


@router.post("/{knowledge_base_id}/documents", response_model=KnowledgeDocument)
async def upload_document(
    knowledge_base_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    tenant_id: TenantHeader = None,
) -> KnowledgeDocument:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.KNOWLEDGE_MANAGE,
        )
    value = await _get_base(request, access.tenant.id, knowledge_base_id)
    provider = _provider_for(request, value)
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "document_too_large", "message": "文档不能超过 50 MB"},
        )
    document = await provider.upload_document(
        dataset_id=value.provider_id,
        filename=file.filename or "document",
        content=content,
        content_type=file.content_type or "application/octet-stream",
    )
    await provider.start_parsing(dataset_id=value.provider_id, document_ids=[document.provider_id])
    return document


@router.get("/{knowledge_base_id}/documents", response_model=list[KnowledgeDocument])
async def list_documents(
    knowledge_base_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[KnowledgeDocument]:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=None,
        )
    value = await _get_base(request, access.tenant.id, knowledge_base_id)
    return await _provider_for(request, value).list_documents(dataset_id=value.provider_id)


@router.post("/{knowledge_base_id}/retrieve", response_model=KnowledgeSearchResult)
async def retrieve(
    knowledge_base_id: UUID,
    payload: RetrieveRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> KnowledgeSearchResult:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=None,
        )
    value = await _get_base(request, access.tenant.id, knowledge_base_id)
    return await _provider_for(request, value).retrieve(
        question=payload.question,
        dataset_ids=[value.provider_id],
        page_size=payload.limit,
        metadata_condition=payload.metadata_condition,
    )
