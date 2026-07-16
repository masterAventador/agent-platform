from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.api.routes.auth import CredentialsRequest
from agent_platform.bootstrap.demo_seed import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_MEMBERSHIP_ID,
    DEMO_ADMIN_USER_ID,
    DEMO_ARTIFACT_CONTENT,
    DEMO_ARTIFACT_ID,
    DEMO_ATTACHMENT_ID,
    DEMO_COMPLETED_RUN_ID,
    DEMO_DEAD_LETTER_ID,
    DEMO_DRAFT_EMPLOYEE_ID,
    DEMO_EMAIL,
    DEMO_EMPLOYEE_ID,
    DEMO_FILE_CONTENT,
    DEMO_FILE_ID,
    DEMO_MCP_SERVER_ID,
    DEMO_MEMBER_EMAIL,
    DEMO_MEMBER_MEMBERSHIP_ID,
    DEMO_MEMBER_USER_ID,
    DEMO_MEMBERSHIP_ID,
    DEMO_PASSWORD,
    DEMO_TENANT_ID,
    DEMO_TOOL_ID,
    DEMO_USER_ID,
    DEMO_WORKSPACE_NAME,
    DemoSeedSafetyError,
    seed_demo_data,
    validate_demo_database_url,
)
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models
from agent_platform.infrastructure.database.repositories.artifacts import (
    ArtifactRecord,
    FileRecord,
    TaskAttachmentRecord,
)
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
)
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.infrastructure.database.repositories.runs import RunEventRecord, RunRecord
from agent_platform.infrastructure.database.repositories.skills import SkillRecord
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.infrastructure.database.repositories.tools import McpServerRecord, ToolRecord
from agent_platform.infrastructure.security.passwords import Argon2PasswordHasher
from agent_platform.platform.runs.entities import RunStatus

ALLOWED_DEMO_DATABASE_URL = "postgresql+asyncpg://demo:secret@127.0.0.1:5432/agent_platform_demo"


class MemoryArtifactStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, content: bytes, media_type: str) -> None:
        del media_type
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


def test_demo_credentials_are_accepted_by_the_login_contract() -> None:
    credentials = CredentialsRequest(email=DEMO_EMAIL, password=DEMO_PASSWORD)

    assert str(credentials.email) == DEMO_EMAIL


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    load_database_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://demo:secret@localhost:5432/agent_platform",
        "postgresql+asyncpg://demo:secret@localhost:5432/agent_platform_dev",
        "postgresql+asyncpg://demo:secret@127.0.0.1:5432/demo",
        "postgresql+asyncpg://demo:secret@[::1]:5432/agent_platform_e2e",
    ],
)
def test_demo_seed_allows_only_explicit_local_demo_databases(database_url: str) -> None:
    validate_demo_database_url(database_url, environment="development")


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://demo:secret@database.internal:5432/agent_platform_demo",
        ("postgresql+asyncpg://demo:secret@127.0.0.1:5432/agent_platform?host=database.internal"),
        "postgresql+asyncpg://demo:secret@localhost:5432/postgres",
        "postgresql+asyncpg://demo:secret@127.0.0.1:5432/template1",
        "sqlite+aiosqlite:///agent_platform_demo.db",
    ],
)
def test_demo_seed_refuses_remote_or_non_demo_databases(database_url: str) -> None:
    with pytest.raises(DemoSeedSafetyError, match="refused"):
        validate_demo_database_url(database_url, environment="development")


@pytest.mark.parametrize("environment", ["production", "staging", "test"])
def test_demo_seed_refuses_non_development_environments(environment: str) -> None:
    with pytest.raises(DemoSeedSafetyError, match="refused"):
        validate_demo_database_url(ALLOWED_DEMO_DATABASE_URL, environment=environment)


@pytest.mark.asyncio
async def test_demo_seed_is_stable_idempotent_login_ready_and_has_no_external_dangling_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = MemoryArtifactStorage()
    first = await seed_demo_data(
        session_factory=session_factory,
        database_url=ALLOWED_DEMO_DATABASE_URL,
        environment="development",
        artifact_storage=storage,
    )
    second = await seed_demo_data(
        session_factory=session_factory,
        database_url=ALLOWED_DEMO_DATABASE_URL,
        environment="development",
        artifact_storage=storage,
    )

    assert first.created > 0
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == first.created
    assert second.email == DEMO_EMAIL
    assert second.admin_email == DEMO_ADMIN_EMAIL
    assert second.member_email == DEMO_MEMBER_EMAIL
    assert second.password == DEMO_PASSWORD
    assert second.workspace_name == DEMO_WORKSPACE_NAME

    async with session_factory() as session:
        assert await _count(session, UserRecord) == 3
        assert await _count(session, TenantRecord) == 1
        assert await _count(session, TenantMembershipRecord) == 3
        assert await _count(session, EmployeeRecord) == 2
        assert await _count(session, EmployeeVersionRecord) == 1
        assert await _count(session, RunRecord) == 2
        assert await _count(session, RunEventRecord) == 7
        assert await _count(session, FileRecord) == 1
        assert await _count(session, TaskAttachmentRecord) == 1
        assert await _count(session, ArtifactRecord) == 1
        assert await _count(session, McpServerRecord) == 1
        assert await _count(session, ToolRecord) == 1
        assert await _count(session, RunDeadLetterRecord) == 1
        assert await _count(session, SkillRecord) == 0
        assert await _count(session, KnowledgeBaseRecord) == 0

        user = await session.get(UserRecord, DEMO_USER_ID)
        admin = await session.get(UserRecord, DEMO_ADMIN_USER_ID)
        member = await session.get(UserRecord, DEMO_MEMBER_USER_ID)
        owner_membership = await session.get(TenantMembershipRecord, DEMO_MEMBERSHIP_ID)
        admin_membership = await session.get(TenantMembershipRecord, DEMO_ADMIN_MEMBERSHIP_ID)
        member_membership = await session.get(TenantMembershipRecord, DEMO_MEMBER_MEMBERSHIP_ID)
        tenant = await session.get(TenantRecord, DEMO_TENANT_ID)
        employee = await session.get(EmployeeRecord, DEMO_EMPLOYEE_ID)
        draft_employee = await session.get(EmployeeRecord, DEMO_DRAFT_EMPLOYEE_ID)
        server = await session.get(McpServerRecord, DEMO_MCP_SERVER_ID)
        tool = await session.get(ToolRecord, DEMO_TOOL_ID)
        dead_letter = await session.get(RunDeadLetterRecord, DEMO_DEAD_LETTER_ID)
        assert user is not None and user.email == DEMO_EMAIL
        assert Argon2PasswordHasher().verify(DEMO_PASSWORD, user.password_hash)
        assert admin is not None and admin.email == DEMO_ADMIN_EMAIL
        assert Argon2PasswordHasher().verify(DEMO_PASSWORD, admin.password_hash)
        assert member is not None and member.email == DEMO_MEMBER_EMAIL
        assert Argon2PasswordHasher().verify(DEMO_PASSWORD, member.password_hash)
        assert owner_membership is not None and owner_membership.role == "owner"
        assert admin_membership is not None and admin_membership.role == "admin"
        assert member_membership is not None and member_membership.role == "member"
        assert {
            owner_membership.tenant_id,
            admin_membership.tenant_id,
            member_membership.tenant_id,
        } == {DEMO_TENANT_ID}
        assert tenant is not None and tenant.name == DEMO_WORKSPACE_NAME
        assert employee is not None and employee.published_version == 1
        assert employee.capabilities["file_upload"] is True
        # C05 多轮会话交付后，演示员工必须开箱支持会话，用户验收无需手工开能力
        assert employee.capabilities["conversation"] is True
        assert "Seed 本身不调用模型" in employee.role_description
        assert "手动发起任务" in employee.role_description
        assert "可能产生上游费用" in employee.role_description
        assert employee.skill_ids == []
        assert employee.tool_ids == []
        assert employee.knowledge_base_ids == []
        assert draft_employee is not None
        assert draft_employee.status == "draft"
        assert draft_employee.visibility == "private"
        assert draft_employee.published_version is None
        assert server is not None and server.enabled is False
        assert tool is not None and tool.enabled is False
        assert dead_letter is not None
        assert dead_letter.raw_fields_summary == {
            "known_field_keys": [],
            "unknown_fields": [],
            "field_count": 0,
            "total_bytes": 0,
            "sha256": None,
        }

        statuses = set((await session.scalars(select(RunRecord.status))).all())
        assert statuses == {RunStatus.COMPLETED.value, RunStatus.FAILED.value}
        completed_run = await session.get(RunRecord, DEMO_COMPLETED_RUN_ID)
        assert completed_run is not None
        assert completed_run.created_by == DEMO_MEMBER_USER_ID
        file = await session.get(FileRecord, DEMO_FILE_ID)
        attachment = await session.get(TaskAttachmentRecord, DEMO_ATTACHMENT_ID)
        artifact = await session.get(ArtifactRecord, DEMO_ARTIFACT_ID)
        assert file is not None and storage.objects[file.storage_key] == DEMO_FILE_CONTENT
        assert attachment is not None and attachment.file_id == DEMO_FILE_ID
        assert artifact is not None
        assert storage.objects[artifact.storage_key] == DEMO_ARTIFACT_CONTENT
        artifact_events = (
            await session.scalars(
                select(RunEventRecord).where(
                    RunEventRecord.run_id == DEMO_COMPLETED_RUN_ID,
                    RunEventRecord.event_type == "artifact.created",
                )
            )
        ).all()
        assert len(artifact_events) == 1
        assert artifact_events[0].payload["artifact_id"] == str(DEMO_ARTIFACT_ID)

        employee.name = "被本地修改的名称"
        await session.commit()

    repaired = await seed_demo_data(
        session_factory=session_factory,
        database_url=ALLOWED_DEMO_DATABASE_URL,
        environment="development",
        artifact_storage=storage,
    )
    assert repaired.created == 0
    assert repaired.updated == 1

    async with session_factory() as session:
        employee = await session.get(EmployeeRecord, DEMO_EMPLOYEE_ID)
        assert employee is not None and employee.name == "演示研究助理"


async def _count(session: AsyncSession, model: type[Base]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@pytest.mark.asyncio
async def test_demo_seed_grants_social_operations_entitlement_idempotently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import select

    from agent_platform.infrastructure.database.repositories.entitlements import (
        CapabilityEntitlementRecord,
    )

    storage = MemoryArtifactStorage()
    for _ in range(2):
        await seed_demo_data(
            session_factory=session_factory,
            database_url=ALLOWED_DEMO_DATABASE_URL,
            environment="development",
            artifact_storage=storage,
        )

    async with session_factory() as session:
        records = (
            await session.scalars(
                select(CapabilityEntitlementRecord).where(
                    CapabilityEntitlementRecord.tenant_id == DEMO_TENANT_ID
                )
            )
        ).all()

    assert len(records) == 1
    entitlement = records[0]
    assert entitlement.capability_id == "social-operations"
    assert entitlement.status == "active"
    assert entitlement.source == "demo-seed"
    assert entitlement.expires_at is None

@pytest.mark.asyncio
async def test_demo_seed_adopts_migration_backfilled_tool_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """迁移 0028 会用随机 id 为存量工具回填 version=1；Seed 重放必须按业务键
    (tool_id, version) 收编该行，而不是用稳定 id 再插一条撞唯一约束。"""
    from uuid import uuid4

    from agent_platform.bootstrap.demo_seed import DEMO_TOOL_ID
    from agent_platform.infrastructure.database.repositories.tools import ToolVersionRecord

    storage = MemoryArtifactStorage()
    await seed_demo_data(
        session_factory=session_factory,
        database_url=ALLOWED_DEMO_DATABASE_URL,
        environment="development",
        artifact_storage=storage,
    )
    # 模拟迁移回填：把 seed 建的版本行替换成随机 id 的等价行
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(ToolVersionRecord).where(ToolVersionRecord.tool_id == DEMO_TOOL_ID)
            )
        ).scalar_one()
        backfilled = ToolVersionRecord(
            id=uuid4(),
            tenant_id=existing.tenant_id,
            tool_id=existing.tool_id,
            version=existing.version,
            description=existing.description,
            input_schema=existing.input_schema,
            risk_level=existing.risk_level,
            approval_policy=existing.approval_policy,
            change_source=existing.change_source,
            created_at=existing.created_at,
        )
        await session.delete(existing)
        await session.flush()
        session.add(backfilled)
        await session.commit()

    result = await seed_demo_data(
        session_factory=session_factory,
        database_url=ALLOWED_DEMO_DATABASE_URL,
        environment="development",
        artifact_storage=storage,
    )
    assert result.created == 0

    async with session_factory() as session:
        versions = (
            await session.execute(
                select(ToolVersionRecord).where(ToolVersionRecord.tool_id == DEMO_TOOL_ID)
            )
        ).scalars().all()
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_demo_seed_provides_representative_memories_idempotently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Demo Seed 预置代表性长期记忆且幂等，演示员工开箱启用记忆能力。"""
    from agent_platform.infrastructure.database.repositories.memories import MemoryRecord

    storage = MemoryArtifactStorage()
    for _ in range(2):
        await seed_demo_data(
            session_factory=session_factory,
            database_url=ALLOWED_DEMO_DATABASE_URL,
            environment="development",
            artifact_storage=storage,
        )

    async with session_factory() as session:
        memories = (
            await session.scalars(
                select(MemoryRecord).where(MemoryRecord.tenant_id == DEMO_TENANT_ID)
            )
        ).all()
        employee = await session.get(EmployeeRecord, DEMO_EMPLOYEE_ID)
        version = (
            await session.scalars(
                select(EmployeeVersionRecord).where(
                    EmployeeVersionRecord.employee_id == DEMO_EMPLOYEE_ID
                )
            )
        ).one()

    assert employee is not None
    assert employee.capabilities.get("memory") is True
    capabilities = version.definition.get("capabilities")
    assert isinstance(capabilities, dict) and capabilities.get("memory") is True
    scopes = {record.scope for record in memories}
    assert {"tenant", "user", "employee"}.issubset(scopes)
    assert all(record.status == "active" for record in memories)
    contents = {record.content for record in memories}
    assert len(contents) == len(memories)
