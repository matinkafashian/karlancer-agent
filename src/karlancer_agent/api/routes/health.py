"""Dependency-free liveness and dependency-aware readiness."""

from fastapi import APIRouter, Request, Response, status

from karlancer_agent.api.dependencies import ApplicationServices

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, object]:
    services: ApplicationServices = request.app.state.services
    dependencies = await services.readiness.check()
    ready = all(dependencies.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "dependencies": dependencies}

