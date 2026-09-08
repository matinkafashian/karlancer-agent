"""Internal endpoints used by n8n for assisted Telegram review."""

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from karlancer_agent.api.dependencies import ApplicationServices, ApprovalCommands
from karlancer_agent.api.routes.projects import _authorize, _require_idempotency_key
from karlancer_agent.services.approval import (
    ApprovalCommandResult,
    ApprovalConflict,
    ExpiredApproval,
    InvalidApproval,
    InvalidEditedDraft,
    UsedApproval,
)

router = APIRouter(prefix="/v1/projects/{project_id}/approval", tags=["approvals"])


class CallbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback_data: str = Field(min_length=1, max_length=64)


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1)


def _approval_commands(services: ApplicationServices) -> ApprovalCommands:
    if services.approvals is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="approval service is not configured",
        )
    return services.approvals


@router.post("/report", response_model=ApprovalCommandResult)
async def report(
    project_id: UUID,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> ApprovalCommandResult:
    service = _approval_commands(_authorize(request, internal_key))
    key = _require_idempotency_key(idempotency_key)
    try:
        return await service.request_review(project_id, notification_key=key)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ApprovalConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/callback", response_model=ApprovalCommandResult)
async def callback(
    project_id: UUID,
    body: CallbackBody,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> ApprovalCommandResult:
    service = _approval_commands(_authorize(request, internal_key))
    _require_idempotency_key(idempotency_key)
    try:
        return await service.decide(project_id, callback_data=body.callback_data)
    except ExpiredApproval as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except (UsedApproval, ApprovalConflict) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidApproval as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/edit", response_model=ApprovalCommandResult)
async def edit(
    project_id: UUID,
    body: EditBody,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> ApprovalCommandResult:
    service = _approval_commands(_authorize(request, internal_key))
    key = _require_idempotency_key(idempotency_key)
    try:
        return await service.edit(project_id, body=body.body, notification_key=key)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidEditedDraft as error:
        raise HTTPException(
            status_code=422,
            detail=error.validation.model_dump(mode="json"),
        ) from error
    except ApprovalConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
