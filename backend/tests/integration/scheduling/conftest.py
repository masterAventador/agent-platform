"""C12 调度器测试夹具：真实表结构上的租户/用户/成员/员工发布版本种子。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeStatus,
    EmployeeVersion,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.tenants.memberships import TenantRole

SEED_TIME = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)


def employee_definition(
    *,
    scheduled_tasks: bool = True,
    input_schema: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "name": "巡检员",
        "avatar_url": None,
        "role_description": "定时巡检",
        "visibility": "tenant",
        "work_mode": "autonomous",
        "system_prompt": "你是巡检员",
        "model": {"alias": "qwen-plus"},
        "input_schema": input_schema
        if input_schema is not None
        else {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": True,
            "scheduled_tasks": scheduled_tasks,
            "file_upload": False,
            "memory": False,
        },
        "skill_ids": [],
        "tool_ids": [],
        "knowledge_base_ids": [],
        "knowledge_retrieval": {},
        "approval_policy": {},
        "release_strategy": {"mode": "all"},
        "workflow_id": None,
        "workflow_version": None,
    }


@dataclass(frozen=True)
class SchedulingSeed:
    tenant_id: UUID
    user_id: UUID
    employee_id: UUID
    published_version: int


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    load_database_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_workspace(
    factory: async_sessionmaker,
    *,
    role: TenantRole = TenantRole.MEMBER,
    published: bool = True,
    definition: dict[str, object] | None = None,
) -> SchedulingSeed:
    tenant_id, user_id, employee_id = uuid4(), uuid4(), uuid4()
    async with factory() as session:
        session.add(
            TenantRecord(
                id=tenant_id, name="演示企业", slug=f"tenant-{tenant_id.hex[:8]}",
                created_at=SEED_TIME,
            )
        )
        session.add(
            UserRecord(
                id=user_id,
                email=f"user-{user_id.hex[:8]}@example.com",
                password_hash="x",
                email_verified=True,
                created_at=SEED_TIME,
            )
        )
        session.add(
            TenantMembershipRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role=role.value,
                created_at=SEED_TIME,
            )
        )
        definition_data = definition or employee_definition()
        capabilities = definition_data["capabilities"]
        assert isinstance(capabilities, dict)
        input_schema = definition_data["input_schema"]
        assert isinstance(input_schema, dict)
        draft = EmployeeDraft(
            name="巡检员",
            avatar_url=None,
            role_description="定时巡检",
            visibility=EmployeeVisibility.TENANT,
            runtime_type=RuntimeType.AUTONOMOUS,
            system_prompt="你是巡检员",
            model_settings={"kind": "gateway_alias", "alias": "general-purpose"},
            input_schema=input_schema,
            output_schema={"type": "object"},
            capabilities=capabilities,
            skill_ids=[],
            tool_ids=[],
            knowledge_base_ids=[],
            approval_policy={},
            release_strategy={"mode": "all"},
        )
        employee = Employee.create(tenant_id=tenant_id, created_by=user_id, draft=draft)
        employee = replace(employee, id=employee_id)
        if published:
            employee = replace(employee, status=EmployeeStatus.PUBLISHED, published_version=1)
        await SqlAlchemyEmployeeRepository(session).add(employee)
        if published:
            await SqlAlchemyEmployeeVersionRepository(session).add(
                EmployeeVersion(
                    id=uuid4(),
                    employee_id=employee_id,
                    tenant_id=tenant_id,
                    version=1,
                    definition=draft.snapshot(),
                    published_by=user_id,
                    published_at=SEED_TIME,
                )
            )
        await session.commit()
    return SchedulingSeed(
        tenant_id=tenant_id, user_id=user_id, employee_id=employee_id, published_version=1
    )
