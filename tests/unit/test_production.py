from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from karlancer_agent.browser.session import LoginStatus
from karlancer_agent.config import Settings
from karlancer_agent.production import LazyBrowserController, create_production_app


class FakeBrowser:
    def __init__(self) -> None:
        self.starts = 0
        self.closes = 0
        self.pages = 0

    async def start(self) -> None:
        self.starts += 1

    async def login_status(self) -> LoginStatus:
        return LoginStatus.SIGNED_IN

    @asynccontextmanager
    async def new_page(self):
        self.pages += 1
        yield object()

    async def close(self) -> None:
        self.closes += 1


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="not-configured",
        telegram_chat_id=0,
        internal_api_key="internal-secret-key",
        approval_signing_key="approval-signing-key-with-32-characters",
        browser_profile_dir=tmp_path / "profile",
        model_provider="disabled",
        live_reply_monitor_enabled=False,
    )


@pytest.mark.asyncio
async def test_lazy_browser_starts_once_and_closes_owned_session() -> None:
    browser = FakeBrowser()
    lazy = LazyBrowserController(browser)

    assert await lazy.login_status() is LoginStatus.SIGNED_IN
    async with lazy.new_page():
        pass
    await lazy.close()

    assert browser.starts == 1
    assert browser.pages == 1
    assert browser.closes == 1


@pytest.mark.asyncio
async def test_production_app_exposes_read_only_jobs_but_no_submit_route(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from karlancer_agent.db.base import Base

    configured = settings(tmp_path)
    configured.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'production.sqlite').as_posix()}"
    engine = create_async_engine(configured.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    # Settings validates secrets at construction; preserve that runtime type.
    from pydantic import SecretStr

    configured.database_url = SecretStr(configured.database_url)
    app = create_production_app(configured)
    paths = set(app.openapi()["paths"])

    assert "/v1/jobs/ingest" in paths
    assert "/v1/jobs/shadow" in paths
    assert "/v1/jobs/reply-watch" in paths
    assert not any("submit" in path for path in paths)
    assert app.state.services.settings.submission_enabled is False

    async with app.router.lifespan_context(app):
        pass
