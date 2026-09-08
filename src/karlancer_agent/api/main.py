"""FastAPI application factory; production wiring is added with deployment."""

from fastapi import FastAPI

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.api.routes.approvals import router as approvals_router
from karlancer_agent.api.routes.health import router as health_router
from karlancer_agent.api.routes.projects import router as projects_router


def create_app(services: ApplicationServices) -> FastAPI:
    app = FastAPI(title="Karlancer Agent", version="0.1.0")
    app.state.services = services
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(approvals_router)
    return app
