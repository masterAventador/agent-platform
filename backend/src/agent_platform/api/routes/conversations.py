from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.api.routes.runs import RunResponse
from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyFileRepository,
    SqlAlchemyTaskAttachmentRepository,
)
from agent_platform.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationMessageRepository,
    SqlAlchemyConversationRepository,
)
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.artifacts.entities import File, TaskAttachment
from agent_platform.platform.conversations.entities import (
    MAX_CONVERSATION_MESSAGE_CONTENT_CHARS,
    Conversation,
    ConversationMessage,
    ConversationMessageRole,
)
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeStatus,
    EmployeeVisibility,
    is_runnable_employee_definition,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run, RunStatus
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tenants.permissions import TenantPermission, role_has_permission

router = APIRouter(prefix="/api/v1", tags=["conversations"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]

CONTEXT_MAX_MESSAGES = 8
CONTEXT_MAX_CHARS = MAX_CONVERSATION_MESSAGE_CONTENT_CHARS


class CreateConversationRequest(BaseModel):
    employee_id: UUID
    title: str | None = Field(default=None, max_length=200)


class AppendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONVERSATION_MESSAGE_CONTENT_CHARS)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    dispatch: bool = True


class RetryConversationRequest(BaseModel):
    run_id: UUID | None = None


class ConversationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    created_by: UUID
    title: str
    thread_id: str

    @classmethod
    def from_entity(cls, conversation: Conversation) -> "ConversationResponse":
        return cls(
            id=conversation.id,
            tenant_id=conversation.tenant_id,
            employee_id=conversation.employee_id,
            created_by=conversation.created_by,
            title=conversation.title,
            thread_id=conversation.thread_id,
        )


class ConversationMessageResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID | None
    sequence: int
    role: str
    content: str
    attachment_ids: list[UUID]

    @classmethod
    def from_entity(cls, message: ConversationMessage) -> "ConversationMessageResponse":
        return cls(
            id=message.id,
            tenant_id=message.tenant_id,
            conversation_id=message.conversation_id,
            run_id=message.run_id,
            sequence=message.sequence,
            role=message.role.value,
            content=message.content,
            attachment_ids=list(message.attachment_ids),
        )


class ConversationDetailResponse(ConversationResponse):
    messages: list[ConversationMessageResponse]
    runs: list[RunResponse]


class AppendMessageResponse(BaseModel):
    message: ConversationMessageResponse
    run: RunResponse | None
    run_action: Literal["stored", "started", "message_submitted", "queued_after_current"]


class RetryConversationResponse(BaseModel):
    run: RunResponse


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "resource_not_found", "message": "资源不存在"},
    )


def _can_manage_runs(role: TenantRole) -> bool:
    return role_has_permission(role=role, permission=TenantPermission.RUNS_MANAGE)


def _member_can_see_employee(*, role: TenantRole, employee: Employee) -> bool:
    return _can_manage_runs(role) or (
        employee.status is EmployeeStatus.PUBLISHED
        and employee.published_version is not None
        and employee.draft.visibility is EmployeeVisibility.TENANT
    )


def _ensure_conversation_access(
    *,
    conversation: Conversation | None,
    user_id: UUID,
    can_manage_runs: bool,
) -> Conversation:
    if conversation is None:
        raise _not_found()
    if not can_manage_runs and conversation.created_by != user_id:
        raise _not_found()
    return conversation


async def _load_runnable_employee(
    *,
    database_session: AsyncSession,
    tenant_id: UUID,
    employee_id: UUID,
    role: TenantRole,
) -> tuple[Employee, int]:
    employee = await SqlAlchemyEmployeeRepository(database_session).get(
        tenant_id=tenant_id,
        employee_id=employee_id,
    )
    if employee is None or not _member_can_see_employee(role=role, employee=employee):
        raise _not_found()
    if employee.status is not EmployeeStatus.PUBLISHED or employee.published_version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "employee_not_published", "message": "数字员工尚未发布"},
        )
    version = await SqlAlchemyEmployeeVersionRepository(database_session).get(
        tenant_id=tenant_id,
        employee_id=employee.id,
        version=employee.published_version,
    )
    if version is None or not is_runnable_employee_definition(version.definition):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "employee_configuration_unavailable",
                "message": "数字员工配置当前不可运行",
            },
        )
    capabilities = version.definition.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("conversation") is not True:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conversation_disabled", "message": "数字员工未启用多轮会话"},
        )
    return employee, employee.published_version


async def _validate_attachments(
    *,
    database_session: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    can_manage_runs: bool,
    attachment_ids: list[UUID],
) -> list[File]:
    files = []
    repository = SqlAlchemyFileRepository(database_session)
    for file_id in dict.fromkeys(attachment_ids):
        file = await repository.get(tenant_id=tenant_id, file_id=file_id)
        if file is None or (file.owner_id != user_id and not can_manage_runs):
            raise _not_found()
        files.append(file)
    return files


def _context_payload(messages: Sequence[ConversationMessage]) -> dict[str, JsonValue]:
    return {
        "messages": [
            {
                "role": message.role.value,
                "content": message.content,
                "attachment_ids": [str(value) for value in message.attachment_ids],
            }
            for message in messages
        ],
        "max_messages": CONTEXT_MAX_MESSAGES,
        "max_chars": CONTEXT_MAX_CHARS,
    }


async def _build_run_input(
    *,
    messages: SqlAlchemyConversationMessageRepository,
    conversation: Conversation,
    content: str | None,
    retry_of_run_id: UUID | None = None,
) -> dict[str, JsonValue]:
    context = await messages.list_recent_context(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        max_messages=CONTEXT_MAX_MESSAGES,
        max_chars=CONTEXT_MAX_CHARS,
    )
    payload: dict[str, JsonValue] = {
        "conversation_id": str(conversation.id),
        "message": content or (context[-1].content if context else ""),
        "conversation_context": _context_payload(context),
    }
    if retry_of_run_id is not None:
        payload["retry_of_run_id"] = str(retry_of_run_id)
    return payload


async def _create_run_for_conversation(
    *,
    database_session: AsyncSession,
    conversation: Conversation,
    employee_version: int,
    created_by: UUID,
    input_data: dict[str, JsonValue],
    attachment_files: list[File],
) -> Run:
    run = Run.create(
        tenant_id=conversation.tenant_id,
        employee_id=conversation.employee_id,
        employee_version=employee_version,
        created_by=created_by,
        input_data=input_data,
        conversation_id=conversation.id,
        thread_id=conversation.thread_id,
    )
    await SqlAlchemyRunRepository(database_session).add(run)
    attachments = SqlAlchemyTaskAttachmentRepository(database_session)
    for file in attachment_files:
        await attachments.add(
            TaskAttachment.create(
                tenant_id=run.tenant_id,
                run_id=run.id,
                file_id=file.id,
                workspace_path=f"inputs/{file.id}/{file.name}",
            )
        )
    await SqlAlchemyRunCommandRepository(database_session).add(
        RunCommand.create(run_id=run.id, tenant_id=run.tenant_id, action=RunCommandAction.START)
    )
    return run


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ConversationResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        await _load_runnable_employee(
            database_session=database_session,
            tenant_id=access.tenant.id,
            employee_id=payload.employee_id,
            role=access.role,
        )
        conversation = Conversation.create(
            tenant_id=access.tenant.id,
            employee_id=payload.employee_id,
            created_by=user.id,
            title=payload.title,
        )
        await SqlAlchemyConversationRepository(database_session).add(conversation)
        await database_session.commit()
    return ConversationResponse.from_entity(conversation)


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request,
    tenant_id: TenantHeader = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[ConversationResponse]:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        can_manage = _can_manage_runs(access.role)
        conversations = await SqlAlchemyConversationRepository(database_session).list(
            tenant_id=access.tenant.id,
            created_by=None if can_manage else user.id,
            limit=limit,
        )
    return [ConversationResponse.from_entity(conversation) for conversation in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ConversationDetailResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        conversations = SqlAlchemyConversationRepository(database_session)
        conversation = _ensure_conversation_access(
            conversation=await conversations.get(
                tenant_id=access.tenant.id,
                conversation_id=conversation_id,
            ),
            user_id=user.id,
            can_manage_runs=_can_manage_runs(access.role),
        )
        messages = await SqlAlchemyConversationMessageRepository(database_session).list(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
        )
        runs = await conversations.list_runs(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
        )
    return ConversationDetailResponse(
        **ConversationResponse.from_entity(conversation).model_dump(),
        messages=[ConversationMessageResponse.from_entity(message) for message in messages],
        runs=[RunResponse.from_entity(run) for run in runs],
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AppendMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def append_message(
    conversation_id: UUID,
    payload: AppendMessageRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> AppendMessageResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        can_manage = _can_manage_runs(access.role)
        conversations = SqlAlchemyConversationRepository(database_session)
        conversation = _ensure_conversation_access(
            conversation=await conversations.get(
                tenant_id=access.tenant.id,
                conversation_id=conversation_id,
            ),
            user_id=user.id,
            can_manage_runs=can_manage,
        )
        _, employee_version = await _load_runnable_employee(
            database_session=database_session,
            tenant_id=access.tenant.id,
            employee_id=conversation.employee_id,
            role=access.role,
        )
        attachment_files = await _validate_attachments(
            database_session=database_session,
            tenant_id=access.tenant.id,
            user_id=user.id,
            can_manage_runs=can_manage,
            attachment_ids=payload.attachment_ids,
        )
        messages = SqlAlchemyConversationMessageRepository(database_session)
        message = ConversationMessage.create(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            sequence=await messages.next_sequence(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
            ),
            role=ConversationMessageRole.USER,
            content=payload.content.strip(),
            attachment_ids=tuple(dict.fromkeys(payload.attachment_ids)),
        )
        await messages.add(message)
        await conversations.update(conversation.touch(message.created_at))
        run: Run | None = None
        run_action: Literal["stored", "started", "message_submitted", "queued_after_current"] = (
            "stored"
        )
        if payload.dispatch:
            active = await conversations.latest_active_run(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
            )
            if active is not None and active.status is RunStatus.WAITING_FOR_INPUT:
                run = active
                await messages.bind_run(
                    tenant_id=conversation.tenant_id,
                    conversation_id=conversation.id,
                    message_id=message.id,
                    run_id=run.id,
                )
                message = replace(message, run_id=run.id)
                await SqlAlchemyRunCommandRepository(database_session).add(
                    RunCommand.create(
                        run_id=run.id,
                        tenant_id=run.tenant_id,
                        action=RunCommandAction.MESSAGE,
                        payload={"message": message.content, "requested_by": str(user.id)},
                    )
                )
                run_action = "message_submitted"
            elif active is not None:
                run = active
                run_action = "queued_after_current"
            else:
                run = await _create_run_for_conversation(
                    database_session=database_session,
                    conversation=conversation,
                    employee_version=employee_version,
                    created_by=user.id,
                    input_data=await _build_run_input(
                        messages=messages,
                        conversation=conversation,
                        content=message.content,
                    ),
                    attachment_files=attachment_files,
                )
                await messages.bind_run(
                    tenant_id=conversation.tenant_id,
                    conversation_id=conversation.id,
                    message_id=message.id,
                    run_id=run.id,
                )
                message = replace(message, run_id=run.id)
                run_action = "started"
        await database_session.commit()
    return AppendMessageResponse(
        message=ConversationMessageResponse.from_entity(message),
        run=RunResponse.from_entity(run) if run is not None else None,
        run_action=run_action,
    )


@router.post(
    "/conversations/{conversation_id}/retry",
    response_model=RetryConversationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_conversation(
    conversation_id: UUID,
    payload: RetryConversationRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> RetryConversationResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.RUNS_EXECUTE,
        )
        can_manage = _can_manage_runs(access.role)
        conversations = SqlAlchemyConversationRepository(database_session)
        conversation = _ensure_conversation_access(
            conversation=await conversations.get(
                tenant_id=access.tenant.id,
                conversation_id=conversation_id,
            ),
            user_id=user.id,
            can_manage_runs=can_manage,
        )
        _, employee_version = await _load_runnable_employee(
            database_session=database_session,
            tenant_id=access.tenant.id,
            employee_id=conversation.employee_id,
            role=access.role,
        )
        failed_run = await conversations.latest_failed_run(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            run_id=payload.run_id,
        )
        if failed_run is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "retry_unavailable", "message": "没有可重试的失败任务"},
            )
        messages = SqlAlchemyConversationMessageRepository(database_session)
        run = await _create_run_for_conversation(
            database_session=database_session,
            conversation=conversation,
            employee_version=employee_version,
            created_by=user.id,
            input_data=await _build_run_input(
                messages=messages,
                conversation=conversation,
                content=None,
                retry_of_run_id=failed_run.id,
            ),
            attachment_files=[],
        )
        await database_session.commit()
    return RetryConversationResponse(run=RunResponse.from_entity(run))
