"""长期记忆生命周期 API。

命名空间：企业（tenant）/用户（user）/员工（employee）/会话（conversation）。
所有端点先校验租户成员身份，再按命名空间与角色权限做服务端校验：

- user 级：仅本人可创建；本人可查看/纠正/禁用/删除，管理角色可治理；
- tenant / employee 级：读取对租户成员开放（任务运行时本会注入），
  写入与治理需要 ``employees.manage``；
- conversation 级：仅会话创建者与管理角色可见、可写。

记忆是数据不是指令：本 API 落库的内容只会以数据形式注入运行时，
写入前统一执行敏感信息脱敏 / 受控拒绝。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
)
from agent_platform.infrastructure.database.repositories.memories import (
    SqlAlchemyMemoryRepository,
)
from agent_platform.platform.memory.entities import (
    MAX_MEMORY_CONTENT_CHARS,
    Memory,
    MemoryContentRejected,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    sanitize_memory_content,
)
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission

router = APIRouter(prefix="/api/v1", tags=["memories"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class CreateMemoryRequest(BaseModel):
    scope: MemoryScope
    scope_ref: UUID | None = None
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_CHARS)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: datetime | None = None


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CONTENT_CHARS)
    status: MemoryStatus | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    expires_at: datetime | None = None


class MemoryResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    scope: MemoryScope
    scope_ref: UUID
    content: str
    source: MemorySource
    source_ref: str | None
    confidence: float
    status: MemoryStatus
    expired: bool
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, memory: Memory) -> "MemoryResponse":
        return cls(
            id=memory.id,
            tenant_id=memory.tenant_id,
            scope=memory.scope,
            scope_ref=memory.scope_ref,
            content=memory.content,
            source=memory.source,
            source_ref=memory.source_ref,
            confidence=memory.confidence,
            status=memory.status,
            expired=memory.is_expired(),
            expires_at=memory.expires_at,
            created_by=memory.created_by,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "permission_denied", "message": "没有执行该操作的权限"},
    )


def _can_manage_memories(role: TenantRole) -> bool:
    return role_has_permission(role=role, permission=TenantPermission.EMPLOYEES_MANAGE)


def _sanitized_content(content: str) -> str:
    try:
        sanitized, _ = sanitize_memory_content(content.strip())
    except MemoryContentRejected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "memory_content_rejected",
                "message": "记忆内容整体为敏感数据，已拒绝写入",
            },
        ) from None
    if not sanitized.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "memory_content_rejected", "message": "记忆内容不能为空"},
        )
    return sanitized


async def _resolve_create_namespace(
    *,
    database_session: AsyncSession,
    payload: CreateMemoryRequest,
    tenant_id: UUID,
    user_id: UUID,
    role: TenantRole,
) -> UUID:
    """校验命名空间写入权限并归一 scope_ref。"""

    if payload.scope is MemoryScope.TENANT:
        if not _can_manage_memories(role):
            raise _forbidden()
        return tenant_id
    if payload.scope is MemoryScope.USER:
        if payload.scope_ref is not None and payload.scope_ref != user_id:
            raise _forbidden()
        return user_id
    if payload.scope is MemoryScope.EMPLOYEE:
        if not _can_manage_memories(role):
            raise _forbidden()
        if payload.scope_ref is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_memory_namespace", "message": "缺少员工引用"},
            )
        employee = await SqlAlchemyEmployeeRepository(database_session).get(
            tenant_id=tenant_id,
            employee_id=payload.scope_ref,
        )
        if employee is None:
            raise _not_found()
        return payload.scope_ref
    # conversation scope
    if payload.scope_ref is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_memory_namespace", "message": "缺少会话引用"},
        )
    conversation = await SqlAlchemyConversationRepository(database_session).get(
        tenant_id=tenant_id,
        conversation_id=payload.scope_ref,
    )
    if conversation is None or (
        conversation.created_by != user_id and not _can_manage_memories(role)
    ):
        raise _not_found()
    return payload.scope_ref


async def _load_visible_memory(
    *,
    database_session: AsyncSession,
    tenant_id: UUID,
    memory_id: UUID,
    user_id: UUID,
    role: TenantRole,
) -> Memory:
    """加载记忆并执行可见性裁剪；不可见按资源不存在处理。"""

    memory = await SqlAlchemyMemoryRepository(database_session).get(
        tenant_id=tenant_id,
        memory_id=memory_id,
    )
    if memory is None:
        raise _not_found()
    if _can_manage_memories(role):
        return memory
    if memory.scope in {MemoryScope.TENANT, MemoryScope.EMPLOYEE}:
        return memory
    if memory.scope is MemoryScope.USER:
        if memory.scope_ref == user_id:
            return memory
        raise _not_found()
    conversation = await SqlAlchemyConversationRepository(database_session).get(
        tenant_id=tenant_id,
        conversation_id=memory.scope_ref,
    )
    if conversation is None or conversation.created_by != user_id:
        raise _not_found()
    return memory


def _ensure_governable(*, memory: Memory, user_id: UUID, role: TenantRole) -> None:
    """治理（纠正/禁用/删除）权限：本人的个人级记忆或管理角色。"""

    if _can_manage_memories(role):
        return
    if memory.scope is MemoryScope.USER and memory.scope_ref == user_id:
        return
    if memory.scope is MemoryScope.CONVERSATION:
        # 会话归属已在可见性检查中确认（不可见会话早已 404）。
        return
    raise _forbidden()


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: CreateMemoryRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MemoryResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        scope_ref = await _resolve_create_namespace(
            database_session=database_session,
            payload=payload,
            tenant_id=access.tenant.id,
            user_id=user.id,
            role=access.role,
        )
        memory = Memory.create(
            tenant_id=access.tenant.id,
            scope=payload.scope,
            scope_ref=scope_ref,
            content=_sanitized_content(payload.content),
            source=MemorySource.MANUAL,
            confidence=payload.confidence,
            expires_at=payload.expires_at,
            created_by=user.id,
        )
        stored = await SqlAlchemyMemoryRepository(database_session).upsert(memory)
        await database_session.commit()
    return MemoryResponse.from_entity(stored)


@router.get("/memories", response_model=list[MemoryResponse])
async def list_memories(
    request: Request,
    tenant_id: TenantHeader = None,
    scope: MemoryScope | None = None,
    q: str | None = Query(default=None, max_length=200),
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[MemoryResponse]:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        memories = await SqlAlchemyMemoryRepository(database_session).list(
            tenant_id=access.tenant.id,
            visible_to=None if _can_manage_memories(access.role) else user.id,
            scope=scope,
            keyword=q,
            include_inactive=not active_only,
            limit=limit,
        )
    responses = [MemoryResponse.from_entity(memory) for memory in memories]
    if active_only:
        responses = [response for response in responses if not response.expired]
    return responses


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MemoryResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        memory = await _load_visible_memory(
            database_session=database_session,
            tenant_id=access.tenant.id,
            memory_id=memory_id,
            user_id=user.id,
            role=access.role,
        )
    return MemoryResponse.from_entity(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: UUID,
    payload: UpdateMemoryRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> MemoryResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        memory = await _load_visible_memory(
            database_session=database_session,
            tenant_id=access.tenant.id,
            memory_id=memory_id,
            user_id=user.id,
            role=access.role,
        )
        _ensure_governable(memory=memory, user_id=user.id, role=access.role)
        updated = memory
        if payload.content is not None or payload.confidence is not None:
            updated = updated.correct(
                content=(
                    _sanitized_content(payload.content)
                    if payload.content is not None
                    else None
                ),
                confidence=payload.confidence,
            )
        if payload.expires_at is not None:
            updated = updated.correct(expires_at=payload.expires_at)
        if payload.status is not None and payload.status is not updated.status:
            updated = updated.with_status(payload.status)
        repository = SqlAlchemyMemoryRepository(database_session)
        await repository.update(updated)
        await database_session.commit()
    return MemoryResponse.from_entity(updated)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> None:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        memory = await _load_visible_memory(
            database_session=database_session,
            tenant_id=access.tenant.id,
            memory_id=memory_id,
            user_id=user.id,
            role=access.role,
        )
        _ensure_governable(memory=memory, user_id=user.id, role=access.role)
        # 硬删除：删除后不可恢复召回（运行时与列表均不再返回）。
        await SqlAlchemyMemoryRepository(database_session).delete(
            tenant_id=access.tenant.id,
            memory_id=memory.id,
        )
        await database_session.commit()
