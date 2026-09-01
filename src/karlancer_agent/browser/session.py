"""Single-owner persistent Chromium session and login health checks."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from filelock import FileLock, Timeout
from playwright.async_api import async_playwright

from karlancer_agent.browser.extractor import PageLike

KARLANCER_DASHBOARD_URL = "https://www.karlancer.com/dashboard"


class LoginStatus(StrEnum):
    """Fail-closed authentication state observed through normal page content."""

    SIGNED_IN = "signed_in"
    EXPIRED = "expired"
    CHALLENGE = "challenge"
    UNKNOWN = "unknown"


class BrowserProfileInUseError(RuntimeError):
    """Raised when another worker already owns the persistent profile."""


class BrowserSessionNotStartedError(RuntimeError):
    """Raised when a page is requested before session startup."""


class BrowserPage(PageLike, Protocol):
    async def close(self) -> None: ...


class BrowserContextLike(Protocol):
    async def new_page(self) -> BrowserPage: ...

    async def close(self) -> None: ...


class ChromiumLike(Protocol):
    async def launch_persistent_context(
        self, user_data_dir: str, **kwargs: object
    ) -> BrowserContextLike: ...


class PlaywrightLike(Protocol):
    @property
    def chromium(self) -> ChromiumLike: ...

    async def stop(self) -> None: ...


class PlaywrightManagerLike(Protocol):
    async def start(self) -> PlaywrightLike: ...


PlaywrightFactory = Callable[[], PlaywrightManagerLike]


class BrowserSession:
    """Own exactly one Playwright persistent context for a profile directory."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        headless: bool,
        playwright_factory: PlaywrightFactory | None = None,
    ) -> None:
        self._profile_dir = profile_dir
        self._headless = headless
        self._playwright_factory = playwright_factory or cast(
            PlaywrightFactory, async_playwright
        )
        lock_name = f".{profile_dir.name}.agent.lock"
        self._profile_lock = FileLock(str(profile_dir.parent / lock_name))
        self._playwright: PlaywrightLike | None = None
        self._context: BrowserContextLike | None = None

    async def start(self) -> None:
        """Acquire profile ownership and launch Chromium once."""

        if self._context is not None:
            return
        self._profile_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._profile_lock.acquire(timeout=0)
        except Timeout as error:
            raise BrowserProfileInUseError(
                f"Browser profile is already owned: {self._profile_dir}"
            ) from error

        try:
            manager = self._playwright_factory()
            self._playwright = await manager.start()
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self._profile_dir),
                headless=self._headless,
                viewport=None,
            )
        except BaseException:
            if self._playwright is not None:
                await self._playwright.stop()
            self._playwright = None
            self._profile_lock.release()
            raise

    @asynccontextmanager
    async def new_page(self) -> AsyncIterator[BrowserPage]:
        """Yield an isolated page and always close it after one operation."""

        if self._context is None:
            raise BrowserSessionNotStartedError("Browser session has not been started")
        page = await self._context.new_page()
        try:
            yield page
        finally:
            await page.close()

    async def login_status(self) -> LoginStatus:
        """Check dashboard navigation without reading cookies or stored credentials."""

        async with self.new_page() as page:
            await page.goto(KARLANCER_DASHBOARD_URL, wait_until="domcontentloaded")
            html = (await page.content()).lower()
            final_url = page.url.lower()

        if any(marker in html for marker in ("recaptcha", "captcha", "challenge-platform")):
            return LoginStatus.CHALLENGE
        if "/login" in final_url or 'type="password"' in html:
            return LoginStatus.EXPIRED
        signed_in_markers = ('href="/logout"', "داشبورد من", "حساب کاربری")
        if "/dashboard" in final_url and any(marker in html for marker in signed_in_markers):
            return LoginStatus.SIGNED_IN
        return LoginStatus.UNKNOWN

    async def close(self) -> None:
        """Close Chromium, stop Playwright, and release profile ownership."""

        context, self._context = self._context, None
        playwright, self._playwright = self._playwright, None
        try:
            if context is not None:
                await context.close()
        finally:
            try:
                if playwright is not None:
                    await playwright.stop()
            finally:
                if self._profile_lock.is_locked:
                    self._profile_lock.release()
