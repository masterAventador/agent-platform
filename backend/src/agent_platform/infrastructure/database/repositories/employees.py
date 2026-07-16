from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Uuid, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeStatus,
    EmployeeVersion,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.employees.errors import EmployeeNameAlreadyExists


class EmployeeRecord(Base):
    __tablename__ = "employees"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
    )
    name: Mapped[str] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    role_description: Mapped[str] = mapped_column(String(2000))
    visibility: Mapped[str] = mapped_column(String(32))
    runtime_type: Mapped[str] = mapped_column(String(32))
    system_prompt: Mapped[str] = mapped_column(String(20_000))
    model_settings: Mapped[dict[str, object]] = mapped_column(JSON)
    input_schema: Mapped[dict[str, object]] = mapped_column(JSON)
    output_schema: Mapped[dict[str, object]] = mapped_column(JSON)
    capabilities: Mapped[dict[str, bool]] = mapped_column(JSON)
    skill_ids: Mapped[list[str]] = mapped_column(JSON)
    tool_ids: Mapped[list[str]] = mapped_column(JSON)
    knowledge_base_ids: Mapped[list[str]] = mapped_column(JSON)
    knowledge_retrieval: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    approval_policy: Mapped[dict[str, object]] = mapped_column(JSON)
    release_strategy: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_employees_tenant_name_lower", tenant_id, func.lower(name), unique=True),
    )


class EmployeeVersionRecord(Base):
    __tablename__ = "employee_versions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    employee_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        index=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, object]] = mapped_column(JSON)
    published_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_employee_versions_number", employee_id, version, unique=True),)


class SqlAlchemyEmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, employee: Employee) -> None:
        self._session.add(self._to_record(employee))
        await self._flush_name_constraint()

    async def update(self, employee: Employee) -> None:
        result = await self._session.execute(
            select(EmployeeRecord).where(
                EmployeeRecord.id == employee.id,
                EmployeeRecord.tenant_id == employee.tenant_id,
            )
        )
        record = result.scalar_one()
        record.name = employee.draft.name
        record.avatar_url = employee.draft.avatar_url
        record.role_description = employee.draft.role_description
        record.visibility = employee.draft.visibility.value
        record.runtime_type = employee.draft.runtime_type.value
        record.system_prompt = employee.draft.system_prompt
        record.model_settings = employee.draft.model_settings
        record.input_schema = employee.draft.input_schema
        record.output_schema = employee.draft.output_schema
        record.capabilities = employee.draft.capabilities
        record.skill_ids = [str(value) for value in employee.draft.skill_ids]
        record.tool_ids = [str(value) for value in employee.draft.tool_ids]
        record.knowledge_base_ids = [str(value) for value in employee.draft.knowledge_base_ids]
        record.knowledge_retrieval = employee.draft.knowledge_retrieval
        record.approval_policy = employee.draft.approval_policy
        record.release_strategy = employee.draft.release_strategy
        record.status = employee.status.value
        record.published_version = employee.published_version
        record.updated_at = employee.updated_at
        await self._flush_name_constraint()

    async def get(self, *, tenant_id: UUID, employee_id: UUID) -> Employee | None:
        result = await self._session.execute(
            select(EmployeeRecord).where(
                EmployeeRecord.id == employee_id,
                EmployeeRecord.tenant_id == tenant_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list(self, *, tenant_id: UUID) -> list[Employee]:
        result = await self._session.execute(
            select(EmployeeRecord)
            .where(EmployeeRecord.tenant_id == tenant_id)
            .order_by(EmployeeRecord.created_at)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def _flush_name_constraint(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise EmployeeNameAlreadyExists from error

    @staticmethod
    def _to_record(employee: Employee) -> EmployeeRecord:
        return EmployeeRecord(
            id=employee.id,
            tenant_id=employee.tenant_id,
            created_by=employee.created_by,
            name=employee.draft.name,
            avatar_url=employee.draft.avatar_url,
            role_description=employee.draft.role_description,
            visibility=employee.draft.visibility.value,
            runtime_type=employee.draft.runtime_type.value,
            system_prompt=employee.draft.system_prompt,
            model_settings=employee.draft.model_settings,
            input_schema=employee.draft.input_schema,
            output_schema=employee.draft.output_schema,
            capabilities=employee.draft.capabilities,
            skill_ids=[str(value) for value in employee.draft.skill_ids],
            tool_ids=[str(value) for value in employee.draft.tool_ids],
            knowledge_base_ids=[str(value) for value in employee.draft.knowledge_base_ids],
            knowledge_retrieval=employee.draft.knowledge_retrieval,
            approval_policy=employee.draft.approval_policy,
            release_strategy=employee.draft.release_strategy,
            status=employee.status.value,
            published_version=employee.published_version,
            created_at=employee.created_at,
            updated_at=employee.updated_at,
        )

    @classmethod
    def _to_entity(cls, record: EmployeeRecord) -> Employee:
        return Employee(
            id=record.id,
            tenant_id=record.tenant_id,
            created_by=record.created_by,
            draft=EmployeeDraft(
                name=record.name,
                avatar_url=record.avatar_url,
                role_description=record.role_description,
                visibility=EmployeeVisibility(record.visibility),
                runtime_type=RuntimeType(record.runtime_type),
                system_prompt=record.system_prompt,
                model_settings=record.model_settings,
                input_schema=record.input_schema,
                output_schema=record.output_schema,
                capabilities=record.capabilities,
                skill_ids=[UUID(value) for value in record.skill_ids],
                tool_ids=[UUID(value) for value in record.tool_ids],
                knowledge_base_ids=[UUID(value) for value in record.knowledge_base_ids],
                knowledge_retrieval=record.knowledge_retrieval or {},
                approval_policy=record.approval_policy,
                release_strategy=record.release_strategy,
            ),
            status=EmployeeStatus(record.status),
            published_version=record.published_version,
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyEmployeeVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: EmployeeVersion) -> None:
        self._session.add(
            EmployeeVersionRecord(
                id=version.id,
                employee_id=version.employee_id,
                tenant_id=version.tenant_id,
                version=version.version,
                definition=version.definition,
                published_by=version.published_by,
                published_at=version.published_at,
            )
        )
        await self._session.flush()

    async def list(self, *, tenant_id: UUID, employee_id: UUID) -> list[EmployeeVersion]:
        result = await self._session.execute(
            select(EmployeeVersionRecord)
            .where(
                EmployeeVersionRecord.tenant_id == tenant_id,
                EmployeeVersionRecord.employee_id == employee_id,
            )
            .order_by(EmployeeVersionRecord.version.desc())
        )
        return [
            EmployeeVersion(
                id=record.id,
                employee_id=record.employee_id,
                tenant_id=record.tenant_id,
                version=record.version,
                definition=record.definition,
                published_by=record.published_by,
                published_at=SqlAlchemyEmployeeRepository._as_utc(record.published_at),
            )
            for record in result.scalars()
        ]

    async def get(
        self, *, tenant_id: UUID, employee_id: UUID, version: int
    ) -> EmployeeVersion | None:
        result = await self._session.execute(
            select(EmployeeVersionRecord).where(
                EmployeeVersionRecord.tenant_id == tenant_id,
                EmployeeVersionRecord.employee_id == employee_id,
                EmployeeVersionRecord.version == version,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return EmployeeVersion(
            id=record.id,
            employee_id=record.employee_id,
            tenant_id=record.tenant_id,
            version=record.version,
            definition=record.definition,
            published_by=record.published_by,
            published_at=SqlAlchemyEmployeeRepository._as_utc(record.published_at),
        )
