from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.api.dependencies.authentication import resolve_workspace
from agent_platform.observability.metrics import OperationalComponent, OperationalMetrics

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])
TenantHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


class ClientEventRequest(BaseModel):
    """Bounded client signal; business content and identifiers are deliberately forbidden."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["page", "interaction", "api", "sse", "error"]
    outcome: Literal["succeeded", "failed", "denied", "timeout"]
    duration_ms: float = Field(ge=0, le=300_000)


@router.post("/client-events", status_code=status.HTTP_204_NO_CONTENT)
async def record_client_event(
    payload: ClientEventRequest,
    request: Request,
    tenant_id: TenantHeader = None,
) -> Response:
    async with request.app.state.session_factory() as database_session:
        await resolve_workspace(
            request=request,
            database_session=database_session,
            tenant_id=tenant_id,
            required_permission=None,
        )
    metrics = cast(OperationalMetrics | None, request.app.state.telemetry.operational_metrics)
    if metrics is not None:
        metrics.record(
            component=OperationalComponent.CLIENT,
            operation=payload.operation,
            outcome=payload.outcome,
            duration_ms=payload.duration_ms,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
