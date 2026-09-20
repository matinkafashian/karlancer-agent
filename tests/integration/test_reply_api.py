import httpx
import pytest

from karlancer_agent.config import Settings
from karlancer_agent.reply_api import create_reply_api
from karlancer_agent.runtime import ReplyWatchLoginError
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult


def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="secret",
        telegram_chat_id=1,
        internal_api_key="internal-secret-key",
        approval_signing_key="approval-signing-key-with-32-characters",
    )


class FakeRunner:
    def __init__(self, *, login_error: bool = False) -> None:
        self.calls = 0
        self.closed = False
        self.login_error = login_error

    async def run_once(self) -> ReplyWatchRunResult:
        self.calls += 1
        if self.login_error:
            raise ReplyWatchLoginError("Karlancer login is not usable: expired")
        return ReplyWatchRunResult(projects_checked=2, notifications_sent=1)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_reply_watch_endpoint_requires_secret_and_runs_once() -> None:
    runner = FakeRunner()
    app = create_reply_api(settings=settings(), runner=runner)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post("/v1/jobs/reply-watch")
        accepted = await client.post(
            "/v1/jobs/reply-watch",
            headers={"X-Internal-API-Key": "internal-secret-key"},
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {"projects_checked": 2, "notifications_sent": 1}
    assert runner.calls == 1
    assert runner.closed


@pytest.mark.asyncio
async def test_reply_watch_endpoint_fails_closed_for_expired_login() -> None:
    runner = FakeRunner(login_error=True)
    app = create_reply_api(settings=settings(), runner=runner)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/jobs/reply-watch",
            headers={"X-Internal-API-Key": "internal-secret-key"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Karlancer login requires attention"
