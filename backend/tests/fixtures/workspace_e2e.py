from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    SqlAlchemyTenantRepository,
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.tenants.entities import Tenant

load_database_models()


def _isolated_e2e_database_url() -> str:
    database_url = os.environ["AGENT_PLATFORM_DATABASE_URL"]
    if make_url(database_url).database != "agent_platform_e2e":
        raise RuntimeError("workspace fixture only accepts the isolated E2E database")
    return database_url


def _employee(
    *,
    tenant_id: UUID,
    user_id: UUID,
    name: str,
) -> Employee:
    return Employee.create(
        tenant_id=tenant_id,
        created_by=user_id,
        draft=EmployeeDraft(
            name=name,
            avatar_url=None,
            role_description=f"{name} 的租户隔离验收数据",
            visibility=EmployeeVisibility.TENANT,
            runtime_type=RuntimeType.AUTONOMOUS,
            system_prompt="仅用于隔离 Playwright E2E 验收。",
            model_settings={"kind": "gateway_alias", "alias": "general-purpose"},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            capabilities={
                "conversation": True,
                "scheduled_tasks": False,
                "file_upload": False,
            },
            skill_ids=[],
            tool_ids=[],
            knowledge_base_ids=[],
            approval_policy={},
            release_strategy={"mode": "all"},
        ),
    )


async def prepare(email: str) -> dict[str, str]:
    engine = create_async_engine(_isolated_e2e_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(UserRecord, TenantMembershipRecord, TenantRecord)
                .join(
                    TenantMembershipRecord,
                    TenantMembershipRecord.user_id == UserRecord.id,
                )
                .join(TenantRecord, TenantRecord.id == TenantMembershipRecord.tenant_id)
                .where(
                    UserRecord.email == email.strip().lower(),
                    TenantMembershipRecord.role == "owner",
                )
            )
            user, owner_membership, owner_tenant = result.one()
            marker = user.id.hex[:8]
            member_tenant = Tenant.create(
                name=f"成员工作区-{marker}",
                slug=f"e2e-member-{user.id.hex}",
            )
            await SqlAlchemyTenantRepository(session).add(member_tenant)
            session.add(
                TenantMembershipRecord(
                    id=uuid4(),
                    tenant_id=member_tenant.id,
                    user_id=user.id,
                    role="member",
                    created_at=datetime.now(UTC),
                )
            )

            owner_employee = _employee(
                tenant_id=owner_membership.tenant_id,
                user_id=user.id,
                name=f"A区专属员工-{marker}",
            )
            member_employee = _employee(
                tenant_id=member_tenant.id,
                user_id=user.id,
                name=f"B区专属员工-{marker}",
            )
            employees = SqlAlchemyEmployeeRepository(session)
            await employees.add(owner_employee)
            await employees.add(member_employee)
            await session.commit()

            return {
                "owner_workspace_id": str(owner_membership.tenant_id),
                "owner_workspace_name": owner_tenant.name,
                "member_workspace_id": str(member_tenant.id),
                "member_workspace_name": member_tenant.name,
                "owner_employee_id": str(owner_employee.id),
                "owner_employee_name": owner_employee.draft.name,
                "member_employee_id": str(member_employee.id),
                "member_employee_name": member_employee.draft.name,
            }
    finally:
        await engine.dispose()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tests.fixtures.workspace_e2e <registered-email>")
    print(json.dumps(asyncio.run(prepare(sys.argv[1]))))


if __name__ == "__main__":
    main()
