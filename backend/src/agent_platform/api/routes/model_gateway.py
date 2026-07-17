from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    field_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.audit import emit_audit_event
from agent_platform.infrastructure.database.repositories.model_gateway import (
    SqlAlchemyModelGatewayPolicyRepository,
)
from agent_platform.platform.model_gateway.entities import (
    MAX_BUDGET_MICROUSD,
    MAX_SIGNED_INT32,
    ModelGatewayBudgetPeriod,
    ModelGatewayPolicyStatus,
    TenantModelGatewayPolicy,
)
from agent_platform.platform.model_gateway.errors import (
    CorruptModelGatewayPolicy,
    InvalidModelGatewayKey,
    InvalidModelGatewayPolicy,
    ModelGatewayKeyNotProvisioned,
    ModelGatewayKeyRotationInProgress,
    ModelGatewayPolicyNotFound,
    ModelGatewayPolicyPersistenceError,
    ModelGatewayPolicyRevisionConflict,
)
from agent_platform.platform.model_gateway.services import ModelGatewayPolicyService
from agent_platform.platform.models import GatewayAlias
from agent_platform.platform.tenants.permissions import TenantPermission

TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]
DecimalMicrousd = Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
NonNegativeInt32 = Annotated[StrictInt, Field(ge=0, le=MAX_SIGNED_INT32)]
PositiveInt32 = Annotated[StrictInt, Field(gt=0, le=MAX_SIGNED_INT32)]
router = APIRouter(prefix="/api/v1/model-gateway", tags=["model-gateway"])


class ModelGatewayPolicyPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: NonNegativeInt32
    enabled: StrictBool
    allowed_aliases: list[GatewayAlias] = Field(min_length=1)
    budget_microusd: DecimalMicrousd
    budget_period: ModelGatewayBudgetPeriod
    rpm_limit: PositiveInt32
    tpm_limit: PositiveInt32
    max_parallel_requests: PositiveInt32

    @field_validator("budget_microusd")
    @classmethod
    def validate_budget_precision(cls, value: str) -> str:
        if int(value) > MAX_BUDGET_MICROUSD:
            raise ValueError("budget_microusd exceeds safe gateway precision")
        return value

    @field_validator("allowed_aliases")
    @classmethod
    def validate_unique_aliases(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_aliases must be unique")
        return value


class ModelGatewayPolicyResponse(BaseModel):
    enabled: bool
    allowed_aliases: list[str]
    budget_microusd: str
    budget_period: ModelGatewayBudgetPeriod
    rpm_limit: int
    tpm_limit: int
    max_parallel_requests: int
    revision: int
    status: ModelGatewayPolicyStatus
    created_at: datetime
    updated_at: datetime
    updated_by: UUID

    @classmethod
    def from_entity(cls, policy: TenantModelGatewayPolicy) -> "ModelGatewayPolicyResponse":
        return cls(
            enabled=policy.enabled,
            allowed_aliases=sorted(policy.allowed_aliases),
            budget_microusd=str(policy.budget_microusd),
            budget_period=policy.budget_period,
            rpm_limit=policy.rpm_limit,
            tpm_limit=policy.tpm_limit,
            max_parallel_requests=policy.max_parallel_requests,
            revision=policy.revision,
            status=policy.status,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            updated_by=policy.updated_by,
        )


def _service(session: AsyncSession) -> ModelGatewayPolicyService:
    return ModelGatewayPolicyService(SqlAlchemyModelGatewayPolicyRepository(session))


def _raise_policy_error(error: Exception) -> None:
    if isinstance(error, ModelGatewayKeyNotProvisioned):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": error.code, "message": "模型网关凭据尚未签发"},
        ) from error
    if isinstance(error, ModelGatewayKeyRotationInProgress):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": error.code,
                # 逃生舱必须写在运维现场看得到的地方：上一次轮换若永久失败，命令已结算，
                # 不会有任何东西自动重试；重新 PUT 策略会产生新 revision 与新对账命令。
                "message": (
                    "上一版本凭据尚未在网关回收，无法再次轮换。"
                    "若上次对账已失败（策略状态为 error），请重新提交一次模型网关策略"
                    "（PUT /api/v1/model-gateway/policy）以重新入队对账；"
                    "当前租户仍在使用上一个已生效的凭据版本，服务不受影响。"
                ),
            },
        ) from error
    if isinstance(error, ModelGatewayPolicyNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": error.code, "message": "模型网关策略不存在"},
        ) from error
    if isinstance(error, ModelGatewayPolicyRevisionConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": "模型网关策略已被其他请求更新"},
        ) from error
    if isinstance(error, InvalidModelGatewayPolicy):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": error.code, "message": "模型网关策略无效"},
        ) from error
    if isinstance(error, CorruptModelGatewayPolicy):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": error.code,
                "message": "模型网关策略持久化数据无效",
            },
        ) from error
    if isinstance(error, ModelGatewayPolicyPersistenceError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": error.code,
                "message": "模型网关策略持久化失败",
            },
        ) from error
    raise error


@router.get("/policy", response_model=ModelGatewayPolicyResponse)
async def get_model_gateway_policy(
    request: Request, tenant_id: TenantHeader = None
) -> ModelGatewayPolicyResponse:
    async with request.app.state.session_factory() as session:
        _, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.MODELS_USAGE_READ,
        )
        try:
            policy = await _service(session).get(access.tenant.id)
        except (
            ModelGatewayPolicyNotFound,
            CorruptModelGatewayPolicy,
            ModelGatewayPolicyPersistenceError,
        ) as error:
            _raise_policy_error(error)
            raise AssertionError("unreachable") from error
    return ModelGatewayPolicyResponse.from_entity(policy)


@router.put("/policy", response_model=ModelGatewayPolicyResponse)
async def put_model_gateway_policy(
    payload: ModelGatewayPolicyPut,
    request: Request,
    tenant_id: TenantHeader = None,
) -> ModelGatewayPolicyResponse:
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.MODELS_MANAGE,
        )
        try:
            policy = await _service(session).put_desired(
                tenant_id=access.tenant.id,
                updated_by=user.id,
                expected_revision=payload.expected_revision,
                enabled=payload.enabled,
                allowed_aliases=set(payload.allowed_aliases),
                budget_microusd=int(payload.budget_microusd),
                budget_period=payload.budget_period.value,
                rpm_limit=payload.rpm_limit,
                tpm_limit=payload.tpm_limit,
                max_parallel_requests=payload.max_parallel_requests,
            )
            # C16 完成定义：模型配置变更接入 C14 统一审计。metadata 只记录
            # provider-neutral 的策略字段，绝不含供应商、真实模型名或任何凭据材料。
            audit_metadata: dict[str, JsonValue] = {
                "revision": policy.revision,
                "enabled": policy.enabled,
                "allowed_aliases": list[JsonValue](sorted(policy.allowed_aliases)),
                "budget_microusd": str(policy.budget_microusd),
                "budget_period": policy.budget_period.value,
                "rpm_limit": policy.rpm_limit,
                "tpm_limit": policy.tpm_limit,
                "max_parallel_requests": policy.max_parallel_requests,
            }
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="model_gateway.policy_updated",
                resource_type="model_gateway_policy",
                resource_id=access.tenant.id,
                metadata=audit_metadata,
            )
            await session.commit()
        except (
            InvalidModelGatewayPolicy,
            ModelGatewayPolicyRevisionConflict,
            ModelGatewayPolicyPersistenceError,
        ) as error:
            _raise_policy_error(error)
            raise AssertionError("unreachable") from error
    return ModelGatewayPolicyResponse.from_entity(policy)


@router.post("/key/rotate", response_model=ModelGatewayPolicyResponse)
async def rotate_model_gateway_key(
    request: Request,
    tenant_id: TenantHeader = None,
) -> ModelGatewayPolicyResponse:
    """轮换本租户的网关虚拟 Key。

    只递增 desired 的 Key 版本并重新入队对账；新旧 Key 明文都不返回给调用者——
    只有持有派生密钥的 Controller 与 Worker 能在进程内派生它。

    在途 Run 的语义：Run 在启动时已解析并持有旧版本 Key，Controller 完成对账后旧 Key
    会在网关侧被删除，这些 Run 的后续模型调用会失败（不静默降级）。轮换是安全操作，
    优先保证撤销的即时性，不为在途 Run 保留旧凭据。

    「版本先落库再触达网关」的代价：若随后的对账**永久失败**，DB 已停在新版本而网关侧只有
    旧版本，此时 ``retired_key_version`` 不会被清空，再次轮换会持续 409，直到重新 PUT 策略
    产生新的对账命令。这不影响服务——凭据解析用的是 observed 版本，租户继续使用网关侧真实
    可用的旧版本。选择这个代价是因为反过来（先动网关再落库）会在崩溃时留下无人回收的孤儿 Key。
    """
    async with request.app.state.session_factory() as session:
        user, access = await resolve_workspace(
            request=request,
            database_session=session,
            tenant_id=tenant_id,
            required_permission=TenantPermission.MODELS_MANAGE,
        )
        try:
            policy, key = await _service(session).rotate_key(
                tenant_id=access.tenant.id,
                rotated_by=user.id,
            )
            await emit_audit_event(
                session,
                tenant_id=access.tenant.id,
                actor_user_id=user.id,
                action="model_gateway.key_rotated",
                resource_type="model_gateway_key",
                resource_id=access.tenant.id,
                # 只记录版本号：Key 明文与摘要都绝不进入审计。
                metadata={"key_version": key.key_version},
            )
            await session.commit()
        except (
            ModelGatewayPolicyNotFound,
            ModelGatewayKeyNotProvisioned,
            ModelGatewayKeyRotationInProgress,
            InvalidModelGatewayKey,
            ModelGatewayPolicyRevisionConflict,
            ModelGatewayPolicyPersistenceError,
        ) as error:
            _raise_policy_error(error)
            raise AssertionError("unreachable") from error
    return ModelGatewayPolicyResponse.from_entity(policy)
