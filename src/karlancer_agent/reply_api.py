"""Private HTTP surface used by n8n to schedule reply checks."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, status

from karlancer_agent.config import Settings
from karlancer_agent.runtime import (
    ReplyWatchLoginError,
    build_reply_watch_application,
)
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult


class ReplyWatchRunner(Protocol):
    async def run_once(self) -> ReplyWatchRunResult: ...

    async def close(self) -> None: ...


def create_reply_api(
    *,
    settings: Settings | None = None,
    runner: ReplyWatchRunner | None = None,
) -> FastAPI:
    """Create an internal-only app; no route can message a Karlancer employer."""

    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    resolved_runner = runner or build_reply_watch_application(resolved_settings)
    run_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await resolved_runner.close()

    app = FastAPI(title="Karlancer Reply Watcher", version="0.1.0", lifespan=lifespan)

    def authorize(supplied: str | None) -> None:
        expected = resolved_settings.internal_api_key.get_secret_value()
        if supplied is None or not secrets.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid internal API key",
            )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/jobs/reply-watch", response_model=ReplyWatchRunResult)
    async def reply_watch(
        internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
    ) -> ReplyWatchRunResult:
        authorize(internal_key)
        if run_lock.locked():
            return ReplyWatchRunResult(projects_checked=0, notifications_sent=0)
        try:
            async with run_lock:
                return await resolved_runner.run_once()
        except ReplyWatchLoginError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Karlancer login requires attention",
            ) from error

    return app
