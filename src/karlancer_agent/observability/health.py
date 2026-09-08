"""Independent readiness probes for critical dependencies."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from karlancer_agent.browser.session import LoginStatus


class DatabaseHealth(Protocol):
    async def ping(self) -> bool: ...


class BrowserHealth(Protocol):
    async def login_status(self) -> LoginStatus: ...


class ReadinessService:
    def __init__(
        self,
        *,
        database: DatabaseHealth,
        browser: BrowserHealth,
        provider_probe: Callable[[], Awaitable[bool]],
    ) -> None:
        self._database = database
        self._browser = browser
        self._provider_probe = provider_probe

    async def check(self) -> dict[str, bool]:
        return {
            "database": await self._database.ping(),
            "browser": await self._browser.login_status() is LoginStatus.SIGNED_IN,
            "provider": await self._provider_probe(),
        }

