from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_platform.config import AppSettings
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata
from agent_platform.infrastructure.database.repositories.auth import UserRecord
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
)
from agent_platform.infrastructure.database.repositories.runs import RunEventRecord, RunRecord
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.infrastructure.database.repositories.tools import McpServerRecord, ToolRecord
from agent_platform.infrastructure.security.passwords import Argon2PasswordHasher
from agent_platform.platform.employees.entities import (
    EmployeeStatus,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.runs.entities import RunStatus
from agent_platform.platform.runs.events import EventType
from agent_platform.platform.tenants.memberships import TenantRole
from agent_platform.platform.tools.entities import McpTransport, ToolRiskLevel

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "agent-platform-demo"
DEMO_WORKSPACE_NAME = "Agent Platform 演示工作区"

_DEMO_NAMESPACE = UUID("6934bbce-08a3-4d77-a49c-cfbe395d20b0")
DEMO_USER_ID = uuid5(_DEMO_NAMESPACE, "user")
DEMO_TENANT_ID = uuid5(_DEMO_NAMESPACE, "tenant")
DEMO_MEMBERSHIP_ID = uuid5(_DEMO_NAMESPACE, "membership")
DEMO_EMPLOYEE_ID = uuid5(_DEMO_NAMESPACE, "employee")
DEMO_EMPLOYEE_VERSION_ID = uuid5(_DEMO_NAMESPACE, "employee-version-1")
DEMO_COMPLETED_RUN_ID = uuid5(_DEMO_NAMESPACE, "completed-run")
DEMO_FAILED_RUN_ID = uuid5(_DEMO_NAMESPACE, "failed-run")
DEMO_MCP_SERVER_ID = uuid5(_DEMO_NAMESPACE, "disabled-mcp-server")
DEMO_TOOL_ID = uuid5(_DEMO_NAMESPACE, "disabled-tool")
DEMO_DEAD_LETTER_ID = uuid5(_DEMO_NAMESPACE, "settled-dead-letter")

_DEMO_CREATED_AT = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
_DEMO_STARTED_AT = datetime(2026, 7, 1, 8, 1, tzinfo=UTC)
_DEMO_FINISHED_AT = datetime(2026, 7, 1, 8, 2, tzinfo=UTC)
_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ALLOWED_ENVIRONMENTS = frozenset({"local", "development"})
_SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})


class DemoSeedSafetyError(RuntimeError):
    """Raised when the target is not an explicitly local development database."""


class DemoSeedConflict(RuntimeError):
    """Raised when a natural key belongs to data outside the stable demo IDs."""


@dataclass(frozen=True, slots=True)
class DemoSeedSummary:
    email: str
    password: str
    workspace_name: str
    created: int
    updated: int
    unchanged: int


type DemoRecord = (
    UserRecord
    | TenantRecord
    | TenantMembershipRecord
    | EmployeeRecord
    | EmployeeVersionRecord
    | RunRecord
    | RunEventRecord
    | McpServerRecord
    | ToolRecord
    | RunDeadLetterRecord
)


def validate_demo_database_url(database_url: str, *, environment: str) -> None:
    try:
        parsed = make_url(database_url)
    except ArgumentError as error:
        raise DemoSeedSafetyError(
            "demo seed refused for this environment or database target"
        ) from error
    database_name = (parsed.database or "").lower()
    has_host_override = any(key.lower() == "host" for key in parsed.query)
    if (
        environment not in _ALLOWED_ENVIRONMENTS
        or parsed.drivername != "postgresql+asyncpg"
        or parsed.host not in _ALLOWED_HOSTS
        or has_host_override
        or not database_name
        or database_name in _SYSTEM_DATABASES
    ):
        raise DemoSeedSafetyError("demo seed refused for this environment or database target")


async def seed_demo_data(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    database_url: str,
    environment: str,
) -> DemoSeedSummary:
    validate_demo_database_url(database_url, environment=environment)
    hasher = Argon2PasswordHasher()
    created = 0
    updated = 0
    unchanged = 0

    async with session_factory() as session:
        password_hash = await _demo_password_hash(session, hasher)
        for desired, mutable_fields in _demo_records(password_hash):
            was_created, was_updated = await _upsert_record(
                session,
                desired=desired,
                mutable_fields=mutable_fields,
            )
            if was_created:
                created += 1
            elif was_updated:
                updated += 1
            else:
                unchanged += 1
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise DemoSeedConflict("stable demo data conflicts with existing local data") from error

    return DemoSeedSummary(
        email=DEMO_EMAIL,
        password=DEMO_PASSWORD,
        workspace_name=DEMO_WORKSPACE_NAME,
        created=created,
        updated=updated,
        unchanged=unchanged,
    )


async def _demo_password_hash(session: AsyncSession, hasher: Argon2PasswordHasher) -> str:
    existing = await session.get(UserRecord, DEMO_USER_ID)
    if existing is not None and hasher.verify(DEMO_PASSWORD, existing.password_hash):
        return existing.password_hash
    return hasher.hash(DEMO_PASSWORD)


async def _upsert_record(
    session: AsyncSession,
    *,
    desired: DemoRecord,
    mutable_fields: tuple[str, ...],
) -> tuple[bool, bool]:
    identity = desired.event_id if isinstance(desired, RunEventRecord) else desired.id
    existing = cast(DemoRecord | None, await session.get(type(desired), identity))
    if existing is None:
        session.add(desired)
        return True, False
    changed = False
    for field in mutable_fields:
        desired_value = getattr(desired, field)
        if not _seed_values_equal(getattr(existing, field), desired_value):
            setattr(existing, field, desired_value)
            changed = True
    return False, changed


def _seed_values_equal(existing: object, desired: object) -> bool:
    if isinstance(existing, datetime) and isinstance(desired, datetime):
        existing_utc = existing if existing.tzinfo is not None else existing.replace(tzinfo=UTC)
        desired_utc = desired if desired.tzinfo is not None else desired.replace(tzinfo=UTC)
        return existing_utc.astimezone(UTC) == desired_utc.astimezone(UTC)
    return existing == desired


def _demo_records(password_hash: str) -> list[tuple[DemoRecord, tuple[str, ...]]]:
    employee_definition: dict[str, object] = {
        "name": "演示研究助理",
        "avatar_url": None,
        "role_description": "展示数字员工定义与历史任务，不触发外部模型调用。",
        "visibility": EmployeeVisibility.TENANT.value,
        "work_mode": RuntimeType.AUTONOMOUS.value,
        "system_prompt": "这是本地演示员工，仅用于查看平台能力。",
        "model": {"provider": "demo", "name": "disabled"},
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "capabilities": {
            "conversation": False,
            "scheduled_tasks": False,
            "file_upload": False,
        },
        "skill_ids": [],
        "tool_ids": [],
        "knowledge_base_ids": [],
        "approval_policy": {},
        "release_strategy": {"mode": "all"},
    }
    records: list[tuple[DemoRecord, tuple[str, ...]]] = [
        (
            UserRecord(
                id=DEMO_USER_ID,
                email=DEMO_EMAIL,
                password_hash=password_hash,
                email_verified=False,
                created_at=_DEMO_CREATED_AT,
            ),
            ("email", "password_hash", "email_verified"),
        ),
        (
            TenantRecord(
                id=DEMO_TENANT_ID,
                name=DEMO_WORKSPACE_NAME,
                slug="agent-platform-demo",
                created_at=_DEMO_CREATED_AT,
            ),
            ("name", "slug"),
        ),
        (
            TenantMembershipRecord(
                id=DEMO_MEMBERSHIP_ID,
                tenant_id=DEMO_TENANT_ID,
                user_id=DEMO_USER_ID,
                role=TenantRole.OWNER.value,
                created_at=_DEMO_CREATED_AT,
            ),
            ("tenant_id", "user_id", "role"),
        ),
        (
            EmployeeRecord(
                id=DEMO_EMPLOYEE_ID,
                tenant_id=DEMO_TENANT_ID,
                created_by=DEMO_USER_ID,
                name="演示研究助理",
                avatar_url=None,
                role_description="展示数字员工定义与历史任务，不触发外部模型调用。",
                visibility=EmployeeVisibility.TENANT.value,
                runtime_type=RuntimeType.AUTONOMOUS.value,
                system_prompt="这是本地演示员工，仅用于查看平台能力。",
                model_settings={"provider": "demo", "name": "disabled"},
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                capabilities={
                    "conversation": False,
                    "scheduled_tasks": False,
                    "file_upload": False,
                },
                skill_ids=[],
                tool_ids=[],
                knowledge_base_ids=[],
                approval_policy={},
                release_strategy={"mode": "all"},
                status=EmployeeStatus.PUBLISHED.value,
                published_version=1,
                created_at=_DEMO_CREATED_AT,
                updated_at=_DEMO_CREATED_AT,
            ),
            (
                "tenant_id",
                "created_by",
                "name",
                "avatar_url",
                "role_description",
                "visibility",
                "runtime_type",
                "system_prompt",
                "model_settings",
                "input_schema",
                "output_schema",
                "capabilities",
                "skill_ids",
                "tool_ids",
                "knowledge_base_ids",
                "approval_policy",
                "release_strategy",
                "status",
                "published_version",
            ),
        ),
        (
            EmployeeVersionRecord(
                id=DEMO_EMPLOYEE_VERSION_ID,
                employee_id=DEMO_EMPLOYEE_ID,
                tenant_id=DEMO_TENANT_ID,
                version=1,
                definition=employee_definition,
                published_by=DEMO_USER_ID,
                published_at=_DEMO_CREATED_AT,
            ),
            ("employee_id", "tenant_id", "version", "definition", "published_by"),
        ),
        *_demo_run_records(),
        (
            McpServerRecord(
                id=DEMO_MCP_SERVER_ID,
                tenant_id=DEMO_TENANT_ID,
                name="演示企业搜索（未启用）",
                transport=McpTransport.STREAMABLE_HTTP.value,
                endpoint="http://127.0.0.1:9/demo-disabled",
                command=None,
                args=[],
                secret_reference=None,
                enabled=False,
                created_by=DEMO_USER_ID,
                created_at=_DEMO_CREATED_AT,
                updated_at=_DEMO_CREATED_AT,
            ),
            (
                "tenant_id",
                "name",
                "transport",
                "endpoint",
                "command",
                "args",
                "secret_reference",
                "enabled",
                "created_by",
            ),
        ),
        (
            ToolRecord(
                id=DEMO_TOOL_ID,
                tenant_id=DEMO_TENANT_ID,
                server_id=DEMO_MCP_SERVER_ID,
                name="search_demo_documents",
                description="禁用的本地演示工具，不会发起外部调用。",
                input_schema={"type": "object", "properties": {}},
                risk_level=ToolRiskLevel.READ.value,
                enabled=False,
                created_at=_DEMO_CREATED_AT,
                updated_at=_DEMO_CREATED_AT,
            ),
            (
                "tenant_id",
                "server_id",
                "name",
                "description",
                "input_schema",
                "risk_level",
                "enabled",
            ),
        ),
        (
            RunDeadLetterRecord(
                id=DEMO_DEAD_LETTER_ID,
                source_stream="agent-platform:demo:runs",
                original_delivery_id="demo-malformed-delivery",
                original_command_id=None,
                original_run_id=DEMO_FAILED_RUN_ID,
                tenant_id=DEMO_TENANT_ID,
                action=None,
                attempts=3,
                error_type="DemoDeliveryFailure",
                is_malformed=True,
                raw_fields_summary={
                    "known_field_keys": [],
                    "unknown_fields": [],
                    "field_count": 0,
                    "total_bytes": 0,
                    "sha256": None,
                },
                failed_at=_DEMO_FINISHED_AT,
                replayed_run_id=None,
                replayed_command_id=None,
                replayed_at=None,
                settled_run_id=DEMO_FAILED_RUN_ID,
                mirrored_at=_DEMO_FINISHED_AT,
            ),
            (
                "source_stream",
                "original_delivery_id",
                "original_command_id",
                "original_run_id",
                "tenant_id",
                "action",
                "attempts",
                "error_type",
                "is_malformed",
                "raw_fields_summary",
                "replayed_run_id",
                "replayed_command_id",
                "replayed_at",
                "settled_run_id",
                "mirrored_at",
            ),
        ),
    ]
    return records


def _demo_run_records() -> list[tuple[DemoRecord, tuple[str, ...]]]:
    run_fields = (
        "tenant_id",
        "employee_id",
        "employee_version",
        "created_by",
        "thread_id",
        "input_data",
        "status",
        "started_at",
        "finished_at",
        "error_code",
        "error_message",
    )
    event_fields = (
        "event_version",
        "tenant_id",
        "employee_id",
        "run_id",
        "sequence",
        "event_type",
        "occurred_at",
        "payload",
    )
    records: list[tuple[DemoRecord, tuple[str, ...]]] = [
        (
            RunRecord(
                id=DEMO_COMPLETED_RUN_ID,
                tenant_id=DEMO_TENANT_ID,
                employee_id=DEMO_EMPLOYEE_ID,
                employee_version=1,
                created_by=DEMO_USER_ID,
                thread_id=str(DEMO_COMPLETED_RUN_ID),
                input_data={"topic": "企业级 AI Agent 平台演示"},
                status=RunStatus.COMPLETED.value,
                created_at=_DEMO_CREATED_AT,
                updated_at=_DEMO_FINISHED_AT,
                started_at=_DEMO_STARTED_AT,
                finished_at=_DEMO_FINISHED_AT,
                error_code=None,
                error_message=None,
            ),
            run_fields,
        ),
        (
            RunRecord(
                id=DEMO_FAILED_RUN_ID,
                tenant_id=DEMO_TENANT_ID,
                employee_id=DEMO_EMPLOYEE_ID,
                employee_version=1,
                created_by=DEMO_USER_ID,
                thread_id=str(DEMO_FAILED_RUN_ID),
                input_data={"topic": "失败状态演示"},
                status=RunStatus.FAILED.value,
                created_at=_DEMO_CREATED_AT,
                updated_at=_DEMO_FINISHED_AT,
                started_at=_DEMO_STARTED_AT,
                finished_at=_DEMO_FINISHED_AT,
                error_code="demo_dependency_unavailable",
                error_message="演示依赖未启用",
            ),
            run_fields,
        ),
    ]
    completed_events = (
        (1, EventType.RUN_STARTED, {"status": "running"}),
        (2, EventType.MESSAGE_OUTPUT, {"content": "演示任务已完成，未调用外部模型。"}),
        (3, EventType.RUN_COMPLETED, {"status": "completed"}),
    )
    failed_events = (
        (1, EventType.RUN_STARTED, {"status": "running"}),
        (2, EventType.RUN_PROGRESS, {"message": "正在检查演示依赖"}),
        (3, EventType.RUN_FAILED, {"code": "demo_dependency_unavailable"}),
    )
    for run_id, events in (
        (DEMO_COMPLETED_RUN_ID, completed_events),
        (DEMO_FAILED_RUN_ID, failed_events),
    ):
        for sequence, event_type, payload in events:
            records.append(
                (
                    RunEventRecord(
                        event_id=uuid5(_DEMO_NAMESPACE, f"{run_id}-event-{sequence}"),
                        event_version="1.0",
                        tenant_id=DEMO_TENANT_ID,
                        employee_id=DEMO_EMPLOYEE_ID,
                        run_id=run_id,
                        sequence=sequence,
                        event_type=event_type.value,
                        occurred_at=(
                            _DEMO_STARTED_AT if sequence == 1 else _DEMO_FINISHED_AT
                        ),
                        payload=payload,
                    ),
                    event_fields,
                )
            )
    return records


def _format_summary(summary: DemoSeedSummary) -> str:
    return "\n".join(
        (
            "Demo Seed 完成",
            f"账号: {summary.email}",
            f"密码: {summary.password}",
            f"工作区: {summary.workspace_name}",
            (
                "幂等摘要: "
                f"created={summary.created}, updated={summary.updated}, "
                f"unchanged={summary.unchanged}"
            ),
        )
    )


async def _run_cli(settings: AppSettings) -> DemoSeedSummary:
    initialize_database_metadata()
    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return await seed_demo_data(
            session_factory=session_factory,
            database_url=settings.database_url,
            environment=settings.app_environment,
        )
    finally:
        await engine.dispose()


def main() -> None:
    try:
        summary = asyncio.run(_run_cli(AppSettings()))
    except (DemoSeedSafetyError, DemoSeedConflict) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
    print(_format_summary(summary))


if __name__ == "__main__":
    main()
