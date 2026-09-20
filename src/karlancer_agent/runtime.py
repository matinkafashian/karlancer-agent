"""Production wiring for one idempotent reply-watcher run."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from karlancer_agent.browser.replies import KarlancerReplyReader, ReplyPage
from karlancer_agent.browser.session import BrowserSession, LoginStatus
from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory
from karlancer_agent.notifications.telegram import TelegramBotClient
from karlancer_agent.services.outbox import OutboxDispatcher
from karlancer_agent.services.reply_watcher import ReplyWatcher, ReplyWatchRunResult


class ReplyWatchLoginError(RuntimeError):
    """The authenticated Karlancer profile cannot safely read conversations."""


class BrowserController(Protocol):
    async def start(self) -> None: ...

    async def login_status(self) -> LoginStatus: ...

    def new_page(self) -> AbstractAsyncContextManager[ReplyPage]: ...

    async def close(self) -> None: ...


class ReplyWatchService(Protocol):
    async def run(self, *, page: ReplyPage) -> ReplyWatchRunResult: ...


class PauseReader(Protocol):
    async def is_system_paused(self) -> bool: ...


class ReplyWatchApplication:
    """Own runtime resources and execute a single n8n-scheduled poll."""

    def __init__(
        self,
        *,
        browser: BrowserController,
        watcher: ReplyWatchService,
        engine: AsyncEngine | None = None,
        http: httpx.AsyncClient | None = None,
        pause_controller: PauseReader | None = None,
    ) -> None:
        self._browser = browser
        self._watcher = watcher
        self._engine = engine
        self._http = http
        self._pause_controller = pause_controller

    async def run_once(self) -> ReplyWatchRunResult:
        if self._pause_controller is not None and await self._pause_controller.is_system_paused():
            return ReplyWatchRunResult(projects_checked=0, notifications_sent=0)
        await self._browser.start()
        status = await self._browser.login_status()
        if status is not LoginStatus.SIGNED_IN:
            raise ReplyWatchLoginError(f"Karlancer login is not usable: {status.value}")
        async with self._browser.new_page() as page:
            return await self._watcher.run(page=page)

    async def close(self) -> None:
        try:
            await self._browser.close()
        finally:
            try:
                if self._http is not None:
                    await self._http.aclose()
            finally:
                if self._engine is not None:
                    await self._engine.dispose()


def build_reply_watch_application(settings: Settings) -> ReplyWatchApplication:
    """Build real PostgreSQL, Playwright, and Telegram dependencies."""

    engine = create_engine(settings)
    repository = ProjectRepository(create_session_factory(engine))
    http = httpx.AsyncClient()
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=settings.browser_headless,
        proxy=settings.browser_proxy,
    )
    telegram = TelegramBotClient(
        token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
        http=http,
    )
    watcher = ReplyWatcher(
        repository=repository,
        reader=KarlancerReplyReader(),
        telegram=telegram,
        pause_controller=repository,
        dispatch=OutboxDispatcher(repository=repository, telegram=telegram).dispatch,
    )
    return ReplyWatchApplication(
        browser=browser,
        watcher=watcher,
        engine=engine,
        http=http,
        pause_controller=repository,
    )
