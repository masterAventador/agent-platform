from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.infrastructure.database.repositories.employees import (
    SqlAlchemyEmployeeRepository,
    SqlAlchemyEmployeeVersionRepository,
)
from agent_platform.infrastructure.database.repositories.skills import SqlAlchemySkillRepository
from agent_platform.infrastructure.database.repositories.tools import SqlAlchemyToolRepository
from agent_platform.platform.employees.entities import (
    Employee,
    EmployeeDraft,
    EmployeeVersion,
    EmployeeVisibility,
    RuntimeType,
)
from agent_platform.platform.employees.errors import (
    EmployeeConfigurationUnavailable,
    EmployeeNameAlreadyExists,
    EmployeeNotFound,
    EmployeeSkillNotBindable,
    EmployeeToolNotBindable,
)
from agent_platform.platform.employees.services import EmployeeService

router = APIRouter(prefix="/api/v1/employees", tags=["employees"])


def _default_release_strategy() -> dict[str, object]:
    return {"mode": "all"}


class ModelSettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider: Annotated[str, Field(min_length=1, max_length=100)]
    name: Annotated[str, Field(min_length=1, max_length=200)]


class EmployeeCapabilities(BaseModel):
    conversation: bool = True
    scheduled_tasks: Literal[False] = False
    file_upload: Literal[False] = False


class EmployeeCapabilitiesResponse(BaseModel):
    conversation: bool = True
    scheduled_tasks: bool = False
    file_upload: bool = False


class EmployeeDefinitionBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    avatar_url: AnyHttpUrl | None = None
    role_description: Annotated[str, Field(min_length=1, max_length=2000)]
    visibility: EmployeeVisibility = EmployeeVisibility.TENANT
    system_prompt: Annotated[str, Field(min_length=1, max_length=20_000)]
    model: ModelSettings
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    skill_ids: list[UUID] = Field(default_factory=list)
    tool_ids: list[UUID] = Field(default_factory=list)
    knowledge_base_ids: list[UUID] = Field(default_factory=list)
    approval_policy: dict[str, object] = Field(default_factory=dict)
    release_strategy: dict[str, object] = Field(default_factory=_default_release_strategy)


class EmployeeDefinitionRequest(EmployeeDefinitionBase):
    work_mode: Literal[RuntimeType.AUTONOMOUS]
    capabilities: EmployeeCapabilities

    def to_draft(self) -> EmployeeDraft:
        return EmployeeDraft(
            name=self.name,
            avatar_url=str(self.avatar_url) if self.avatar_url is not None else None,
            role_description=self.role_description,
            visibility=self.visibility,
            runtime_type=self.work_mode,
            system_prompt=self.system_prompt,
            model_settings=self.model.model_dump(),
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            capabilities=self.capabilities.model_dump(),
            skill_ids=self.skill_ids,
            tool_ids=self.tool_ids,
            knowledge_base_ids=self.knowledge_base_ids,
            approval_policy=self.approval_policy,
            release_strategy=self.release_strategy,
        )



class EmployeeDefinitionResponse(EmployeeDefinitionBase):
    work_mode: RuntimeType
    capabilities: EmployeeCapabilitiesResponse

    @classmethod
    def from_draft(cls, draft: EmployeeDraft) -> "EmployeeDefinitionResponse":
        return cls.model_validate(draft.snapshot())


class EmployeeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    status: str
    published_version: int | None
    definition: EmployeeDefinitionResponse

    @classmethod
    def from_entity(cls, employee: Employee) -> "EmployeeResponse":
        return cls(
            id=employee.id,
            tenant_id=employee.tenant_id,
            name=employee.draft.name,
            status=employee.status.value,
            published_version=employee.published_version,
            definition=EmployeeDefinitionResponse.from_draft(employee.draft),
        )


class EmployeeVersionResponse(BaseModel):
    version: int
    definition: dict[str, object]
    published_by: UUID

    @classmethod
    def from_entity(cls, version: EmployeeVersion) -> "EmployeeVersionResponse":
        return cls(
            version=version.version,
            definition=version.definition,
            published_by=version.published_by,
        )


def _service(database_session: AsyncSession) -> EmployeeService:
    return EmployeeService(
        employees=SqlAlchemyEmployeeRepository(database_session),
        versions=SqlAlchemyEmployeeVersionRepository(database_session),
        skills=SqlAlchemySkillRepository(database_session),
        tools=SqlAlchemyToolRepository(database_session),
    )


def _raise_employee_error(error: Exception) -> None:
    if isinstance(error, EmployeeNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "resource_not_found", "message": "资源不存在"},
        ) from error
    if isinstance(error, EmployeeNameAlreadyExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "employee_name_exists", "message": "已存在同名数字员工"},
        ) from error
    if isinstance(error, EmployeeSkillNotBindable):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "skill_not_bindable", "message": "只能绑定本企业已发布的 Skill"},
        ) from error
    if isinstance(error, EmployeeToolNotBindable):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "tool_not_bindable", "message": "只能绑定本企业已启用的 Tool"},
        ) from error
    if isinstance(error, EmployeeConfigurationUnavailable):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "employee_configuration_unavailable",
                "message": "数字员工配置当前不可运行",
            },
        ) from error
    raise error


TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


@router.post("", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeDefinitionRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> EmployeeResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=True,
        )
        try:
            employee = await _service(database_session).create(
                tenant_id=access.tenant.id,
                created_by=user.id,
                draft=payload.to_draft(),
            )
            await database_session.commit()
        except (
            EmployeeNameAlreadyExists,
            EmployeeSkillNotBindable,
            EmployeeToolNotBindable,
        ) as error:
            _raise_employee_error(error)
            raise AssertionError("unreachable") from error
    return EmployeeResponse.from_entity(employee)


@router.get("", response_model=list[EmployeeResponse])
async def list_employees(
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[EmployeeResponse]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        employees = await _service(database_session).list_all(tenant_id=access.tenant.id)
    return [EmployeeResponse.from_entity(employee) for employee in employees]


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> EmployeeResponse:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        try:
            employee = await _service(database_session).get(
                tenant_id=access.tenant.id,
                employee_id=employee_id,
            )
        except EmployeeNotFound as error:
            _raise_employee_error(error)
            raise AssertionError("unreachable") from error
    return EmployeeResponse.from_entity(employee)


@router.put("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: UUID,
    payload: EmployeeDefinitionRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> EmployeeResponse:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=True,
        )
        try:
            employee = await _service(database_session).update(
                tenant_id=access.tenant.id,
                employee_id=employee_id,
                draft=payload.to_draft(),
            )
            await database_session.commit()
        except (
            EmployeeNotFound,
            EmployeeNameAlreadyExists,
            EmployeeSkillNotBindable,
            EmployeeToolNotBindable,
        ) as error:
            _raise_employee_error(error)
            raise AssertionError("unreachable") from error
    return EmployeeResponse.from_entity(employee)


@router.post("/{employee_id}/publish", response_model=EmployeeResponse)
async def publish_employee(
    employee_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> EmployeeResponse:
    async with request.app.state.session_factory() as database_session:
        user, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=True,
        )
        try:
            employee = await _service(database_session).publish(
                tenant_id=access.tenant.id,
                employee_id=employee_id,
                published_by=user.id,
            )
            await database_session.commit()
        except (
            EmployeeNotFound,
            EmployeeConfigurationUnavailable,
            EmployeeSkillNotBindable,
            EmployeeToolNotBindable,
        ) as error:
            _raise_employee_error(error)
            raise AssertionError("unreachable") from error
    return EmployeeResponse.from_entity(employee)


@router.get("/{employee_id}/versions", response_model=list[EmployeeVersionResponse])
async def list_employee_versions(
    employee_id: UUID,
    request: Request,
    tenant_id: TenantHeader = None,
) -> list[EmployeeVersionResponse]:
    async with request.app.state.session_factory() as database_session:
        _, access = await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            owner_required=False,
        )
        try:
            versions = await _service(database_session).list_versions(
                tenant_id=access.tenant.id,
                employee_id=employee_id,
            )
        except EmployeeNotFound as error:
            _raise_employee_error(error)
            raise AssertionError("unreachable") from error
    return [EmployeeVersionResponse.from_entity(version) for version in versions]
