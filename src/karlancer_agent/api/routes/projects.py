"""Idempotent internal commands and sanitized project reads."""

import asyncio
import secrets
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.services.auto_cycle import AutoCycleResult
from karlancer_agent.services.notifications import SystemAlert
from karlancer_agent.services.pipeline import PipelineRunResult, ProjectStageResult

router = APIRouter(prefix="/v1", tags=["projects"])


def _services(request: Request) -> ApplicationServices:
    return cast(ApplicationServices, request.app.state.services)


def _authorize(request: Request, supplied_key: str | None) -> ApplicationServices:
    services = _services(request)
    expected = services.settings.internal_api_key.get_secret_value()
    if supplied_key is None or not secrets.compare_digest(supplied_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal API key",
        )
    return services


def _require_idempotency_key(value: str | None) -> str:
    if value is None or len(value) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key header is required",
        )
    return value


@router.post("/jobs/ingest", response_model=PipelineRunResult)
async def ingest(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> PipelineRunResult:
    services = _authorize(request, internal_key)
    return await services.pipeline.ingest(
        idempotency_key=_require_idempotency_key(idempotency_key)
    )


@router.post("/jobs/shadow", response_model=PipelineRunResult)
async def shadow(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> PipelineRunResult:
    services = _authorize(request, internal_key)
    return await services.pipeline.run_once(
        idempotency_key=_require_idempotency_key(idempotency_key)
    )


@router.post("/jobs/auto", response_model=AutoCycleResult)
async def auto(
    request: Request,
    bootstrap_limit: int | None = Query(default=None, ge=1, le=10),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> AutoCycleResult:
    services = _authorize(request, internal_key)
    if services.auto_cycle is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="automatic cycle is not configured",
        )
    key = _require_idempotency_key(idempotency_key)
    lock = cast(asyncio.Lock, request.app.state.auto_cycle_lock)
    async with lock:
        return await services.auto_cycle.run_once(
            idempotency_key=key, bootstrap_limit=bootstrap_limit,
        )


@router.post("/alerts/operational")
async def operational_alert(
    alert: SystemAlert,
    request: Request,
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> dict[str, bool]:
    services = _authorize(request, internal_key)
    if services.pause_controller is not None:
        changed = await services.pause_controller.pause_system(reason=alert.title)
        if not changed:
            return {"delivered": False}
    if services.operational_notifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="operational notifications are not configured",
        )
    delivered = await services.operational_notifier.queue_and_dispatch(alert)
    return {"delivered": delivered}


@router.post("/projects/{project_id}/analyze", response_model=ProjectStageResult)
async def analyze(
    project_id: UUID,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> ProjectStageResult:
    services = _authorize(request, internal_key)
    _require_idempotency_key(idempotency_key)
    try:
        return await services.pipeline.analyze(project_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/projects/{project_id}/draft", response_model=ProjectStageResult)
async def draft(
    project_id: UUID,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> ProjectStageResult:
    services = _authorize(request, internal_key)
    _require_idempotency_key(idempotency_key)
    try:
        return await services.pipeline.draft(project_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/projects/{project_id}")
async def project(
    project_id: UUID,
    request: Request,
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> dict[str, object]:
    services = _authorize(request, internal_key)
    result = await services.projects.project_view(project_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return result
