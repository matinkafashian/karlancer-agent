"""Read-only first-employer-reply polling endpoint for n8n."""

import asyncio
import secrets
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Request, status

from karlancer_agent.api.dependencies import ApplicationServices
from karlancer_agent.runtime import ReplyWatchLoginError
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult

router = APIRouter(prefix="/v1", tags=["replies"])


@router.post("/jobs/reply-watch", response_model=ReplyWatchRunResult)
async def reply_watch(
    request: Request,
    internal_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> ReplyWatchRunResult:
    services = cast(ApplicationServices, request.app.state.services)
    expected = services.settings.internal_api_key.get_secret_value()
    if internal_key is None or not secrets.compare_digest(internal_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid internal API key",
        )
    if services.reply_watch is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="reply watch is not configured",
        )
    lock = cast(asyncio.Lock, request.app.state.reply_watch_lock)
    if lock.locked():
        return ReplyWatchRunResult(projects_checked=0, notifications_sent=0)
    try:
        async with lock:
            return await services.reply_watch.run_once()
    except ReplyWatchLoginError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Karlancer login requires attention",
        ) from error
