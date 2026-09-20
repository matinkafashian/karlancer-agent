# ruff: noqa: RUF001
"""FastAPI application factory."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.api.routes.approvals import router as approvals_router
from karlancer_agent.api.routes.health import router as health_router
from karlancer_agent.api.routes.projects import router as projects_router
from karlancer_agent.api.routes.replies import router as replies_router
from karlancer_agent.services.notifications import SystemAlert

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    services: ApplicationServices, *, lifespan: Lifespan | None = None
) -> FastAPI:
    app = FastAPI(title="Karlancer Agent", version="0.1.0", lifespan=lifespan)
    app.state.services = services
    app.state.reply_watch_lock = asyncio.Lock()
    app.state.auto_cycle_lock = asyncio.Lock()

    @app.middleware("http")
    async def stop_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        controller = services.pause_controller
        guarded = request.method == "POST" and request.url.path.startswith("/v1/")
        authorized = secrets.compare_digest(
            request.headers.get("X-Internal-API-Key", ""),
            services.settings.internal_api_key.get_secret_value(),
        )
        if controller is None or not guarded or not authorized:
            return await call_next(request)
        if await controller.is_system_paused():
            return JSONResponse({"paused": True, "projects_checked": 0,
                                 "notifications_sent": 0, "submitted": 0})
        try:
            response = await call_next(request)
        except Exception as error:
            response = JSONResponse({"detail": "automation stopped"}, status_code=503)
            reason = f"{type(error).__name__}: {str(error)[:250]}"
        else:
            reason = f"{request.url.path}: HTTP {response.status_code}"
        if response.status_code >= 500:
            changed = await controller.pause_system(reason=reason)
            if changed and services.operational_notifier is not None:
                with suppress(Exception):
                    await services.operational_notifier.queue_and_dispatch(SystemAlert(
                        notification_key=f"system-stop:{datetime.now(UTC).isoformat()}",
                        title="کارلنسر: سیستم متوقف شد",
                        body="اجرای خودکار با خطا روبه‌رو شد. همه فعالیت‌ها متوقف شدند؛ "
                             "ادامه فقط پس از بررسی دستی است. خطا: " + reason,
                    ))
        return response

    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(approvals_router)
    app.include_router(replies_router)
    return app
