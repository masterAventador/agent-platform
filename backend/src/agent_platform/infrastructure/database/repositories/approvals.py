from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.infrastructure.database.base import Base
from agent_platform.platform.approvals.entities import (
    Approval,
    ApprovalSource,
    ApprovalStatus,
)
from agent_platform.platform.approvals.service import ApprovalService
from agent_platform.platform.runs.entities import Run
from agent_platform.platform.runs.events import EventType, PlatformEvent
from agent_platform.platform.tenants.memberships import TenantRole

logger = logging.getLogger(__name__)

_context_adapter = TypeAdapter(dict[str, JsonValue])

TOOL_APPROVAL_TYPE = "tool.invocation"
UNKNOWN_RISK_LEVEL = "unknown"


class MalformedApprovalRequiredEvent(Exception):
    """APPROVAL_REQUIRED 事件缺少可解析的 approval_id，无法建审批记录。"""


class ApprovalRecord(Base):
    __tablename__ = "approvals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32))
    approval_type: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))
    request_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    context: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    required_role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    invocation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    employee_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("employees.id"), nullable=True
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_key: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    transferred_from_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    transferred_to_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "request_key", name="uq_approvals_tenant_request_key"),
    )


class SqlAlchemyApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_idempotent(self, approval: Approval) -> Approval:
        """创建审批记录；同租户同 request_key 重复创建时返回原记录。"""

        if approval.request_key is not None:
            existing = await self.get_by_request_key(
                tenant_id=approval.tenant_id, request_key=approval.request_key
            )
            if existing is not None:
                return existing
        try:
            async with self._session.begin_nested():
                self._session.add(self._to_record(approval))
                await self._session.flush()
        except IntegrityError:
            if approval.request_key is None:
                raise
            existing = await self.get_by_request_key(
                tenant_id=approval.tenant_id, request_key=approval.request_key
            )
            if existing is None:
                raise
            return existing
        return approval

    async def get(self, *, tenant_id: UUID, approval_id: UUID) -> Approval | None:
        record = (
            await self._session.execute(
                select(ApprovalRecord).where(
                    ApprovalRecord.tenant_id == tenant_id,
                    ApprovalRecord.id == approval_id,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def get_by_request_key(
        self, *, tenant_id: UUID, request_key: str
    ) -> Approval | None:
        record = (
            await self._session.execute(
                select(ApprovalRecord).where(
                    ApprovalRecord.tenant_id == tenant_id,
                    ApprovalRecord.request_key == request_key,
                )
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def get_active_for_invocation(
        self, *, tenant_id: UUID, run_id: UUID, invocation_id: UUID
    ) -> Approval | None:
        """同一 run + invocation 的转交链上当前唯一 pending 记录。"""

        record = (
            await self._session.execute(
                select(ApprovalRecord)
                .where(
                    ApprovalRecord.tenant_id == tenant_id,
                    ApprovalRecord.run_id == run_id,
                    ApprovalRecord.invocation_id == invocation_id,
                    ApprovalRecord.status == ApprovalStatus.PENDING.value,
                )
                .order_by(ApprovalRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def get_latest_for_invocation(
        self, *, tenant_id: UUID, run_id: UUID, invocation_id: UUID
    ) -> Approval | None:
        """同一 run + invocation 链上最新记录（不限状态），用于封堵旁路决策。"""

        record = (
            await self._session.execute(
                select(ApprovalRecord)
                .where(
                    ApprovalRecord.tenant_id == tenant_id,
                    ApprovalRecord.run_id == run_id,
                    ApprovalRecord.invocation_id == invocation_id,
                )
                .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return self._to_entity(record) if record is not None else None

    async def list(
        self,
        *,
        tenant_id: UUID,
        statuses: tuple[ApprovalStatus, ...] | None = None,
        assignee_id: UUID | None = None,
        visible_to: UUID | None = None,
        include_unassigned: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Approval], int]:
        conditions = [ApprovalRecord.tenant_id == tenant_id]
        if statuses:
            conditions.append(
                ApprovalRecord.status.in_([status.value for status in statuses])
            )
        if assignee_id is not None:
            conditions.append(ApprovalRecord.assignee_id == assignee_id)
        if visible_to is not None:
            visibility = [
                ApprovalRecord.assignee_id == visible_to,
                ApprovalRecord.requested_by == visible_to,
            ]
            if include_unassigned:
                visibility.append(ApprovalRecord.assignee_id.is_(None))
            conditions.append(or_(*visibility))
        total = (
            await self._session.execute(
                select(func.count()).select_from(ApprovalRecord).where(*conditions)
            )
        ).scalar_one()
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(*conditions)
            .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._to_entity(record) for record in result.scalars()], int(total)

    async def list_pending_for_run(
        self, *, tenant_id: UUID, run_id: UUID
    ) -> Sequence[Approval]:
        result = await self._session.execute(
            select(ApprovalRecord).where(
                ApprovalRecord.tenant_id == tenant_id,
                ApprovalRecord.run_id == run_id,
                ApprovalRecord.status == ApprovalStatus.PENDING.value,
            )
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def list_overdue_pending(self, *, now: datetime, limit: int) -> Sequence[Approval]:
        """跨租户的过期清扫候选：pending 且 expires_at 已过。"""

        result = await self._session.execute(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.status == ApprovalStatus.PENDING.value,
                ApprovalRecord.expires_at.is_not(None),
                ApprovalRecord.expires_at <= now,
            )
            .order_by(ApprovalRecord.expires_at)
            .limit(limit)
        )
        return [self._to_entity(record) for record in result.scalars()]

    async def update_with_cas(
        self, approval: Approval, *, expected_revision: int
    ) -> bool:
        """按 revision CAS 更新；返回是否生效（并发决策只一人生效）。"""

        result = await self._session.execute(
            update(ApprovalRecord)
            .where(
                ApprovalRecord.id == approval.id,
                ApprovalRecord.tenant_id == approval.tenant_id,
                ApprovalRecord.revision == expected_revision,
            )
            .values(
                status=approval.status.value,
                revision=approval.revision,
                updated_at=approval.updated_at,
                assignee_id=approval.assignee_id,
                decided_by=approval.decided_by,
                decision_reason=approval.decision_reason,
                decided_at=approval.decided_at,
                decision_key=approval.decision_key,
                transferred_to_id=approval.transferred_to_id,
            )
        )
        await self._session.flush()
        return isinstance(result, CursorResult) and result.rowcount == 1

    @staticmethod
    def _to_record(approval: Approval) -> ApprovalRecord:
        return ApprovalRecord(
            id=approval.id,
            tenant_id=approval.tenant_id,
            source=approval.source.value,
            approval_type=approval.approval_type,
            risk_level=approval.risk_level,
            requested_by=approval.requested_by,
            request_key=approval.request_key,
            context=approval.context,
            required_role=approval.required_role.value,
            status=approval.status.value,
            revision=approval.revision,
            created_at=approval.created_at,
            updated_at=approval.updated_at,
            run_id=approval.run_id,
            invocation_id=approval.invocation_id,
            employee_id=approval.employee_id,
            assignee_id=approval.assignee_id,
            expires_at=approval.expires_at,
            decided_by=approval.decided_by,
            decision_reason=approval.decision_reason,
            decided_at=approval.decided_at,
            decision_key=approval.decision_key,
            transferred_from_id=approval.transferred_from_id,
            transferred_to_id=approval.transferred_to_id,
        )

    @classmethod
    def _to_entity(cls, record: ApprovalRecord) -> Approval:
        return Approval(
            id=record.id,
            tenant_id=record.tenant_id,
            source=ApprovalSource(record.source),
            approval_type=record.approval_type,
            risk_level=record.risk_level,
            requested_by=record.requested_by,
            context=record.context,
            required_role=TenantRole(record.required_role),
            status=ApprovalStatus(record.status),
            revision=record.revision,
            created_at=cls._as_utc(record.created_at),
            updated_at=cls._as_utc(record.updated_at),
            request_key=record.request_key,
            run_id=record.run_id,
            invocation_id=record.invocation_id,
            employee_id=record.employee_id,
            assignee_id=record.assignee_id,
            expires_at=cls._as_utc(record.expires_at) if record.expires_at else None,
            decided_by=record.decided_by,
            decision_reason=record.decision_reason,
            decided_at=cls._as_utc(record.decided_at) if record.decided_at else None,
            decision_key=record.decision_key,
            transferred_from_id=record.transferred_from_id,
            transferred_to_id=record.transferred_to_id,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def create_approval_service(session: AsyncSession) -> ApprovalService:
    """用 SQLAlchemy 仓储与 C14 审计协议装配平台审批服务。"""

    from agent_platform.infrastructure.database.repositories.audit import (
        emit_audit_event,
    )
    from agent_platform.infrastructure.database.repositories.runs import (
        SqlAlchemyRunCommandRepository,
        SqlAlchemyRunEventRepository,
        SqlAlchemyRunRepository,
    )

    async def audit_sink(**kwargs: object) -> object:
        return await emit_audit_event(session, **kwargs)  # type: ignore[arg-type]

    return ApprovalService(
        approvals=SqlAlchemyApprovalRepository(session),
        runs=SqlAlchemyRunRepository(session),
        run_commands=SqlAlchemyRunCommandRepository(session),
        run_events=SqlAlchemyRunEventRepository(session),
        audit=audit_sink,
    )


def tool_approval_request_key(*, run_id: UUID, approval_id: UUID) -> str:
    return f"tool:{run_id}:{approval_id}"


async def sync_run_approvals(
    session: AsyncSession,
    *,
    run: Run,
    history: Iterable[PlatformEvent],
    pending_timeout: timedelta,
) -> None:
    """把 run 事件流中的 APPROVAL_REQUIRED 事件落为审批记录（幂等）。

    request_key（tool:{run_id}:{approval_id}）保证事件重放/进程重启后
    不重复建记录；风险等级按事件里的工具名从 Tool Registry 反查。
    """

    from agent_platform.infrastructure.database.repositories.audit import (
        emit_audit_event,
    )

    repository = SqlAlchemyApprovalRepository(session)
    for event in history:
        if event.type is not EventType.APPROVAL_REQUIRED:
            continue
        raw_approval_id = event.payload.get("approval_id")
        try:
            approval_id = UUID(str(raw_approval_id))
        except ValueError as error:
            # run 已进等待审批态却建不出审批记录属异常：让投递受控失败（重投/死信），
            # 不静默放行——否则会留下「WAITING_FOR_APPROVAL 却无记录」的 fail-open 窗口。
            logger.error(
                "approval_required_event_without_valid_approval_id",
                extra={"run_id": str(run.id)},
            )
            raise MalformedApprovalRequiredEvent(
                f"run {run.id} APPROVAL_REQUIRED event has invalid approval_id"
            ) from error
        request_key = tool_approval_request_key(run_id=run.id, approval_id=approval_id)
        existing = await repository.get_by_request_key(
            tenant_id=run.tenant_id, request_key=request_key
        )
        if existing is not None:
            continue
        context: dict[str, JsonValue] = {}
        tool_name = event.payload.get("tool_name")
        if isinstance(tool_name, str):
            context["tool_name"] = tool_name
        arguments = event.payload.get("arguments")
        if isinstance(arguments, dict):
            context["arguments"] = _context_adapter.validate_python(arguments)
        reason = event.payload.get("reason")
        if isinstance(reason, str):
            context["reason"] = reason
        approval = Approval.create(
            tenant_id=run.tenant_id,
            source=ApprovalSource.TOOL_RISK,
            approval_type=TOOL_APPROVAL_TYPE,
            risk_level=await _resolve_risk_level(
                session, tenant_id=run.tenant_id, tool_name=tool_name
            ),
            requested_by=run.created_by,
            request_key=request_key,
            context=context,
            run_id=run.id,
            invocation_id=approval_id,
            employee_id=run.employee_id,
            expires_at=datetime.now(UTC) + pending_timeout,
        )
        created = await repository.add_idempotent(approval)
        if created.id == approval.id:
            await emit_audit_event(
                session,
                tenant_id=run.tenant_id,
                actor_user_id=None,
                action="approval.created",
                resource_type="approval",
                resource_id=approval.id,
                metadata={
                    "source": approval.source.value,
                    "run_id": str(run.id),
                    "invocation_id": str(approval_id),
                    "risk_level": approval.risk_level,
                },
            )


async def settle_run_approvals(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    run_id: UUID,
    reason: str,
    invocation_ids: set[UUID] | None = None,
) -> None:
    """把 run 上悬挂的 pending 审批结算为 withdrawn（终态/陈旧结算）。"""

    from agent_platform.infrastructure.database.repositories.audit import (
        emit_audit_event,
    )

    repository = SqlAlchemyApprovalRepository(session)
    for approval in await repository.list_pending_for_run(
        tenant_id=tenant_id, run_id=run_id
    ):
        if invocation_ids is not None and approval.invocation_id not in invocation_ids:
            continue
        settled = approval.withdraw(decided_by=None, reason=reason)
        if not await repository.update_with_cas(
            settled, expected_revision=approval.revision
        ):
            # 与人工决策并发：CAS 失败说明对方已生效，保留其结果。
            continue
        await emit_audit_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=None,
            action="approval.withdrawn",
            resource_type="approval",
            resource_id=approval.id,
            metadata={"reason": reason, "run_id": str(run_id)},
        )


class ApprovalExpirySweepResult:
    def __init__(self) -> None:
        self.expired = 0
        self.failed = 0


async def expire_overdue_approvals(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int,
) -> ApprovalExpirySweepResult:
    """后台清扫：把超时未决策的 pending 审批结算为 expired（配置驱动）。

    取舍说明：过期判定以“决策/读取时惰性判定”为权威（不依赖清扫及时性），
    后台清扫兜底把无人访问的过期记录持久化为终态并驱动 run 拒绝，
    避免任务永远悬挂。单条失败仅记录日志，不影响其余记录。
    """

    result = ApprovalExpirySweepResult()
    async with session_factory() as session:
        candidates = await SqlAlchemyApprovalRepository(session).list_overdue_pending(
            now=now, limit=limit
        )
    for candidate in candidates:
        try:
            async with session_factory() as session:
                settled = await create_approval_service(session).settle_expired(candidate)
                await session.commit()
                if settled.status is ApprovalStatus.EXPIRED:
                    result.expired += 1
        except Exception:
            result.failed += 1
            logger.exception(
                "approval_expiry_sweep_item_failed",
                extra={"approval_id": str(candidate.id)},
            )
    return result


async def _resolve_risk_level(
    session: AsyncSession, *, tenant_id: UUID, tool_name: object
) -> str:
    from agent_platform.infrastructure.database.repositories.tools import ToolRecord

    if not isinstance(tool_name, str):
        return UNKNOWN_RISK_LEVEL
    risk = (
        await session.execute(
            select(ToolRecord.risk_level).where(
                ToolRecord.tenant_id == tenant_id,
                ToolRecord.name == tool_name,
            )
        )
    ).scalar_one_or_none()
    return risk if risk is not None else UNKNOWN_RISK_LEVEL
