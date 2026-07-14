from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
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
    InvalidModelGatewayPolicy,
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
            await session.commit()
        except (
            InvalidModelGatewayPolicy,
            ModelGatewayPolicyRevisionConflict,
            ModelGatewayPolicyPersistenceError,
        ) as error:
            _raise_policy_error(error)
            raise AssertionError("unreachable") from error
    return ModelGatewayPolicyResponse.from_entity(policy)
