from pathlib import Path
from typing import Any

import pytest

from karlancer_agent.browser.session import (
    BrowserProfileInUseError,
    BrowserSession,
    LoginStatus,
)


class FakePage:
    def __init__(self, *, final_url: str, html: str) -> None:
        self.url = "about:blank"
        self._final_url = final_url
        self._html = html
        self.closed = False

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.url = self._final_url

    async def content(self) -> str:
        return self._html

    async def wait_for_timeout(self, timeout: float) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self._pages = pages
        self.closed = False

    async def new_page(self) -> FakePage:
        return self._pages.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def launch_persistent_context(
        self, user_data_dir: str, **kwargs: Any
    ) -> FakeContext:
        self.calls.append((user_data_dir, kwargs))
        return self._context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakeManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self._playwright = playwright
        self.start_calls = 0

    async def start(self) -> FakePlaywright:
        self.start_calls += 1
        return self._playwright


def make_session(
    profile_dir: Path, pages: list[FakePage], proxy: dict[str, str] | None = None,
) -> tuple[BrowserSession, FakeManager, FakeChromium, FakeContext]:
    context = FakeContext(pages)
    chromium = FakeChromium(context)
    playwright = FakePlaywright(chromium)
    manager = FakeManager(playwright)
    session = BrowserSession(
        profile_dir=profile_dir,
        headless=False,
        proxy=proxy,
        playwright_factory=lambda: manager,
    )
    return session, manager, chromium, context


@pytest.mark.asyncio
async def test_persistent_context_launches_exactly_once(tmp_path: Path) -> None:
    session, manager, chromium, context = make_session(tmp_path / "profile", [])

    await session.start()
    await session.start()

    assert manager.start_calls == 1
    assert chromium.calls == [
        (
            str(tmp_path / "profile"),
            {"headless": False, "viewport": None},
        )
    ]

    await session.close()
    assert context.closed is True


@pytest.mark.asyncio
async def test_second_owner_of_same_profile_is_rejected(tmp_path: Path) -> None:
    first, _, _, _ = make_session(tmp_path / "profile", [])
    second, _, _, _ = make_session(tmp_path / "profile", [])
    await first.start()

    with pytest.raises(BrowserProfileInUseError):
        await second.start()

    await first.close()


@pytest.mark.asyncio
async def test_proxy_is_scoped_to_the_dedicated_browser_context(tmp_path: Path) -> None:
    proxy = {"server": "socks5://iran-egress:1080", "username": "u", "password": "p"}
    session, _, chromium, _ = make_session(tmp_path / "profile", [], proxy=proxy)
    await session.start()
    assert chromium.calls == [(
        str(tmp_path / "profile"),
        {"headless": False, "viewport": None, "proxy": proxy},
    )]
    await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("final_url", "html", "expected"),
    [
        (
            "https://www.karlancer.com/panel/messages",
            '<a href="/logout">خروج</a><main>داشبورد من</main>',
            LoginStatus.SIGNED_IN,
        ),
        (
            "https://www.karlancer.com/login?returnUrl=%2Fdashboard",
            '<form><input type="password"></form>',
            LoginStatus.EXPIRED,
        ),
        (
            "https://www.karlancer.com/panel/messages",
            '<iframe title="reCAPTCHA"></iframe>',
            LoginStatus.CHALLENGE,
        ),
    ],
)
async def test_login_health_distinguishes_session_states(
    tmp_path: Path, final_url: str, html: str, expected: LoginStatus
) -> None:
    page = FakePage(final_url=final_url, html=html)
    session, _, _, _ = make_session(tmp_path / expected.value, [page])
    await session.start()

    assert await session.login_status() is expected
    assert page.closed is True

    await session.close()


@pytest.mark.asyncio
async def test_new_page_is_always_closed_after_use(tmp_path: Path) -> None:
    page = FakePage(final_url="about:blank", html="")
    session, _, _, _ = make_session(tmp_path / "profile", [page])
    await session.start()

    async with session.new_page() as opened:
        assert opened is page
        assert page.closed is False

    assert page.closed is True
    await session.close()
