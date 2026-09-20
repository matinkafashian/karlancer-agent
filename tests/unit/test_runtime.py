from contextlib import asynccontextmanager

import pytest

from karlancer_agent.browser.replies import ReplyPage
from karlancer_agent.browser.session import LoginStatus
from karlancer_agent.runtime import ReplyWatchApplication, ReplyWatchLoginError
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult


class FakeBrowser:
    def __init__(self, status: LoginStatus) -> None:
        self.status = status
        self.started = False
        self.closed = False
        self.page = object()

    async def start(self) -> None:
        self.started = True

    async def login_status(self) -> LoginStatus:
        return self.status

    @asynccontextmanager
    async def new_page(self):
        yield self.page

    async def close(self) -> None:
        self.closed = True


class FakeWatcher:
    def __init__(self) -> None:
        self.pages: list[object] = []

    async def run(self, *, page: ReplyPage) -> ReplyWatchRunResult:
        self.pages.append(page)
        return ReplyWatchRunResult(projects_checked=3, notifications_sent=1)


@pytest.mark.asyncio
async def test_runtime_runs_reply_watch_only_with_valid_login() -> None:
    browser = FakeBrowser(LoginStatus.SIGNED_IN)
    watcher = FakeWatcher()
    application = ReplyWatchApplication(browser=browser, watcher=watcher)
    try:
        result = await application.run_once()
    finally:
        await application.close()

    assert result.projects_checked == 3
    assert result.notifications_sent == 1
    assert watcher.pages == [browser.page]
    assert browser.started and browser.closed


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_karlancer_login_expired() -> None:
    browser = FakeBrowser(LoginStatus.EXPIRED)
    watcher = FakeWatcher()
    application = ReplyWatchApplication(browser=browser, watcher=watcher)
    try:
        with pytest.raises(ReplyWatchLoginError, match="expired"):
            await application.run_once()
    finally:
        await application.close()

    assert watcher.pages == []
    assert browser.closed
