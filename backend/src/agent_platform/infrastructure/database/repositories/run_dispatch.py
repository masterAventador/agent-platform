"""Run 的共享创建路径。

API 直跑（`POST /employees/{id}/runs`）、会话轮次派生与 C12 定时调度共用这一份
实现：Run、输入附件与 START 命令的持久化语义只存在一处，调度不建立旁路执行体系。
"""

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.infrastructure.database.repositories.artifacts import (
    SqlAlchemyTaskAttachmentRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.platform.artifacts.entities import File, TaskAttachment
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run


async def create_employee_run(
    *,
    database_session: AsyncSession,
    tenant_id: UUID,
    employee_id: UUID,
    employee_version: int,
    created_by: UUID,
    input_data: dict[str, JsonValue],
    attachment_files: Sequence[File] = (),
    idempotency_key: UUID | None = None,
    conversation_id: UUID | None = None,
    thread_id: str | None = None,
    run_id: UUID | None = None,
) -> Run:
    run = Run.create(
        tenant_id=tenant_id,
        employee_id=employee_id,
        employee_version=employee_version,
        created_by=created_by,
        input_data=input_data,
        idempotency_key=idempotency_key,
        conversation_id=conversation_id,
        thread_id=thread_id,
    )
    if run_id is not None:
        # 调度/派生场景用确定性 run_id；thread_id 默认取 run_id，必须同步替换。
        run = replace(run, id=run_id, thread_id=thread_id or str(run_id))
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
