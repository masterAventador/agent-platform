from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.workflows.entities import (
    Workflow,
    WorkflowStatus,
    WorkflowVersion,
)
from agent_platform.platform.workflows.errors import (
    WorkflowNameAlreadyExists,
    WorkflowVersionAlreadyExists,
)
from agent_platform.platform.workflows.graph_spec import (
    WorkflowGraphSpec,
    parse_workflow_graph,
)


class WorkflowRecord(Base):
    __tablename__ = "workflows"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000))
    latest_version: Mapped[int] = mapped_column(Integer)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_workflows_tenant_name_lower", tenant_id, func.lower(name), unique=True),
    )


class WorkflowVersionRecord(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        index=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(2000))
    graph: Mapped[dict[str, object]] = mapped_column(JSON)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_workflow_versions_number", workflow_id, version, unique=True),
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyWorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, workflow: Workflow) -> None:
        self._session.add(
            WorkflowRecord(
                id=workflow.id,
                tenant_id=workflow.tenant_id,
                created_by=workflow.created_by,
                name=workflow.name,
                description=workflow.description,
                latest_version=workflow.latest_version,
                published_version=workflow.published_version,
                status=workflow.status.value,
                created_at=workflow.created_at,
                updated_at=workflow.updated_at,
            )
        )
        await self._flush_name_constraint()

    async def update(self, workflow: Workflow) -> None:
        result = await self._session.execute(
            select(WorkflowRecord).where(
                WorkflowRecord.id == workflow.id,
                WorkflowRecord.tenant_id == workflow.tenant_id,
            )
        )
        record = result.scalar_one()
        record.name = workflow.name
        record.description = workflow.description
        record.latest_version = workflow.latest_version
        record.published_version = workflow.published_version
        record.status = workflow.status.value
        record.updated_at = workflow.updated_at
        await self._flush_name_constraint()

    async def get(self, *, tenant_id: UUID, workflow_id: UUID) -> Workflow | None:
        result = await self._session.execute(
            select(WorkflowRecord).where(
                WorkflowRecord.id == workflow_id,
                WorkflowRecord.tenant_id == tenant_id,
            )
        )
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list(self, *, tenant_id: UUID) -> list[Workflow]:
        result = await self._session.execute(
            select(WorkflowRecord)
            .where(WorkflowRecord.tenant_id == tenant_id)
            .order_by(WorkflowRecord.created_at)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def _flush_name_constraint(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise WorkflowNameAlreadyExists from error

    @staticmethod
    def _to_entity(record: WorkflowRecord) -> Workflow:
        return Workflow(
            id=record.id,
            tenant_id=record.tenant_id,
            name=record.name,
            description=record.description,
            latest_version=record.latest_version,
            published_version=record.published_version,
            status=WorkflowStatus(record.status),
            created_by=record.created_by,
            created_at=_as_utc(record.created_at),
            updated_at=_as_utc(record.updated_at),
        )


class SqlAlchemyWorkflowVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: WorkflowVersion) -> None:
        self._session.add(
            WorkflowVersionRecord(
                id=version.id,
                workflow_id=version.workflow_id,
                tenant_id=version.tenant_id,
                version=version.version,
                description=version.description,
                graph=version.graph,
                created_by=version.created_by,
                created_at=version.created_at,
                published_at=version.published_at,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as error:
            # 并发 add_version 同时算出相同 latest+1 版本号时，唯一约束保证只一方成功；
            # 落败方转为受控冲突（→409）而非裸 500 且 session 破损。
            await self._session.rollback()
            raise WorkflowVersionAlreadyExists from error

    async def get(
        self, *, tenant_id: UUID, workflow_id: UUID, version: int
    ) -> WorkflowVersion | None:
        result = await self._session.execute(
            select(WorkflowVersionRecord).where(
                WorkflowVersionRecord.tenant_id == tenant_id,
                WorkflowVersionRecord.workflow_id == workflow_id,
                WorkflowVersionRecord.version == version,
            )
        )
        record = result.scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list(self, *, tenant_id: UUID, workflow_id: UUID) -> list[WorkflowVersion]:
        result = await self._session.execute(
            select(WorkflowVersionRecord)
            .where(
                WorkflowVersionRecord.tenant_id == tenant_id,
                WorkflowVersionRecord.workflow_id == workflow_id,
            )
            .order_by(WorkflowVersionRecord.version.desc())
        )
        return [self._to_entity(record) for record in result.scalars()]

    @staticmethod
    def _to_entity(record: WorkflowVersionRecord) -> WorkflowVersion:
        return WorkflowVersion(
            id=record.id,
            workflow_id=record.workflow_id,
            tenant_id=record.tenant_id,
            version=record.version,
            description=record.description,
            graph=record.graph,
            created_by=record.created_by,
            created_at=_as_utc(record.created_at),
            published_at=_as_utc(record.published_at) if record.published_at is not None else None,
        )


class SqlAlchemyEmployeeWorkflowPolicy:
    """员工绑定工作流的注册/发布校验（实现 EmployeeWorkflowPolicy 协议）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._workflows = SqlAlchemyWorkflowRepository(session)

    async def is_registered(self, *, tenant_id: UUID, workflow_id: UUID) -> bool:
        workflow = await self._workflows.get(tenant_id=tenant_id, workflow_id=workflow_id)
        return workflow is not None

    async def published_version(self, *, tenant_id: UUID, workflow_id: UUID) -> int | None:
        workflow = await self._workflows.get(tenant_id=tenant_id, workflow_id=workflow_id)
        if workflow is None:
            return None
        return workflow.published_version


class SqlAlchemyWorkflowSpecLoader:
    """按发布固化的 (workflow_id, version) 加载工作流图并解析为可编译规格。

    实现 worker 侧 WorkflowSpecLoader 协议；版本固化：始终加载指定版本行，
    与工作流当前 published_version 无关（回滚不改变已固化引用的运行语义）。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def load(
        self,
        *,
        tenant_id: UUID,
        workflow_id: UUID,
        version: int,
    ) -> WorkflowGraphSpec | None:
        async with self._session_factory() as session:
            record = await SqlAlchemyWorkflowVersionRepository(session).get(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                version=version,
            )
        if record is None:
            return None
        return parse_workflow_graph(record.graph)
