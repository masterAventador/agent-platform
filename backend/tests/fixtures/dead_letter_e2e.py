from __future__ import annotations

import asyncio
import json
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.runs import (
    SqlAlchemyRunCommandRepository,
    SqlAlchemyRunRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import TenantMembershipRecord
from agent_platform.infrastructure.queue.dead_letters import (
    DELIVERY_PROCESSING_ERROR_TYPE,
    MALFORMED_MESSAGE_ERROR_TYPE,
    RunDeadLetterService,
)
from agent_platform.infrastructure.queue.redis_streams import RunQueueDelivery, RunQueueMessage
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.runs.commands import RunCommand, RunCommandAction
from agent_platform.platform.runs.entities import Run

VALID_PAYLOAD_MARKER = "valid-payload-must-never-appear"
MALFORMED_PAYLOAD_MARKER = "malformed-payload-must-never-appear"


async def prepare(email: str) -> dict[str, str]:
    engine = create_async_engine(os.environ["AGENT_PLATFORM_DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(UserRecord, TenantMembershipRecord)
                .join(
                    TenantMembershipRecord,
                    TenantMembershipRecord.user_id == UserRecord.id,
                )
                .where(UserRecord.email == email.strip().lower())
            )
            row = result.one()
            user, membership = row
            if membership.role != "owner":
                raise RuntimeError("dead-letter E2E fixture requires an owner")

            employee = Employee.create(
                tenant_id=membership.tenant_id,
                created_by=user.id,
                draft=EmployeeDraft(
                    name=f"死信验收员工-{user.id.hex[:8]}",
                    avatar_url=None,
                    role_description="验证死信重放",
                    visibility=EmployeeVisibility.PRIVATE,
                    runtime_type=RuntimeType.AUTONOMOUS,
                    system_prompt="仅用于隔离 E2E 数据准备。",
                    model_settings={},
                    input_schema={},
                    output_schema={},
                    capabilities={},
                    skill_ids=[],
                    tool_ids=[],
                    knowledge_base_ids=[],
                    approval_policy={},
                    release_strategy={},
                ),
            )
            employee, version = employee.publish(published_by=user.id)
            await SqlAlchemyEmployeeRepository(session).add(employee)
            await SqlAlchemyEmployeeVersionRepository(session).add(version)

            valid_run = Run.create(
                tenant_id=membership.tenant_id,
                employee_id=employee.id,
                employee_version=version.version,
                created_by=user.id,
                input_data={"task": "E2E 原任务保持不变"},
            )
            valid_command = RunCommand.create(
                run_id=valid_run.id,
                tenant_id=valid_run.tenant_id,
                action=RunCommandAction.MESSAGE,
                payload={"message": VALID_PAYLOAD_MARKER},
            )
            malformed_run = Run.create(
                tenant_id=membership.tenant_id,
                employee_id=employee.id,
                employee_version=version.version,
                created_by=user.id,
                input_data={"task": "E2E malformed 安全记录"},
            )
            malformed_command = RunCommand.create(
                run_id=malformed_run.id,
                tenant_id=malformed_run.tenant_id,
                action=RunCommandAction.MESSAGE,
            )
            runs = SqlAlchemyRunRepository(session)
            commands = SqlAlchemyRunCommandRepository(session)
            await runs.add(valid_run)
            await commands.add(valid_command)
            await runs.add(malformed_run)
            await commands.add(malformed_command)
            await session.commit()

        service = RunDeadLetterService(session_factory=session_factory)
        valid_dead_letter = await service.record_failure(
            RunQueueDelivery(
                delivery_id=f"e2e-valid-{valid_command.id}",
                message=RunQueueMessage(
                    command_id=valid_command.id,
                    run_id=valid_run.id,
                    tenant_id=valid_run.tenant_id,
                    action=valid_command.action.value,
                    payload=valid_command.payload,
                ),
            ),
            attempts=5,
            error_type=DELIVERY_PROCESSING_ERROR_TYPE,
        )
        malformed_dead_letter = await service.record_malformed(
            delivery_id=f"e2e-malformed-{malformed_command.id}",
            attempts=5,
            error_type=MALFORMED_MESSAGE_ERROR_TYPE,
            raw_fields={
                "command_id": str(malformed_command.id),
                "run_id": str(malformed_run.id),
                "tenant_id": str(malformed_run.tenant_id),
                "action": "message",
                "payload": MALFORMED_PAYLOAD_MARKER,
                "unexpected": "raw-field-value-must-never-appear",
            },
        )
        if valid_dead_letter.settled_run_id != valid_run.id:
            raise RuntimeError("valid dead letter was not settled")
        if malformed_dead_letter.settled_run_id != malformed_run.id:
            raise RuntimeError("malformed dead letter was not tenant-attributed and settled")
        return {
            "tenant_id": str(membership.tenant_id),
            "valid_dead_letter_id": str(valid_dead_letter.id),
            "malformed_dead_letter_id": str(malformed_dead_letter.id),
            "original_run_id": str(valid_run.id),
            "malformed_run_id": str(malformed_run.id),
        }
    finally:
        await engine.dispose()


async def set_workspace_role(email: str, role: str) -> None:
    if role not in {"admin", "member"}:
        raise ValueError("unsupported workspace role")
    engine = create_async_engine(os.environ["AGENT_PLATFORM_DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(TenantMembershipRecord)
                .join(UserRecord, UserRecord.id == TenantMembershipRecord.user_id)
                .where(UserRecord.email == email.strip().lower())
            )
            membership = result.scalar_one()
            membership.role = role
            await session.commit()
    finally:
        await engine.dispose()


def main() -> None:
    if len(sys.argv) == 2:
        print(json.dumps(asyncio.run(prepare(sys.argv[1]))))
        return
    if len(sys.argv) == 4 and sys.argv[1] == "set-role":
        asyncio.run(set_workspace_role(sys.argv[2], sys.argv[3]))
        return
    raise SystemExit(
        "usage: python -m tests.fixtures.dead_letter_e2e <owner-email> | "
        "set-role <email> <admin|member>"
    )


if __name__ == "__main__":
    main()
