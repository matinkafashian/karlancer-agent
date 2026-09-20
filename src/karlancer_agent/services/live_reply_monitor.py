"""Near-live first-reply monitoring on the shared persistent browser."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import datetime
from typing import Any, Literal, Protocol

from karlancer_agent.browser.replies import (
    ReplyActivitySignal,
)
from karlancer_agent.notifications.telegram import TEHRAN
from karlancer_agent.services.notifications import SystemAlert
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult


class LiveBrowser(Protocol):
    def new_page(self) -> AbstractAsyncContextManager[Any]: ...


class LiveWatcher(Protocol):
    async def run(self, *, page: Any) -> ReplyWatchRunResult: ...


class ActivitySignal(Protocol):
    def clear(self) -> None: ...

    async def wait(self, timeout_seconds: float) -> Literal["event", "timeout"]: ...


class AlertNotifier(Protocol):
    async def queue_and_dispatch(self, alert: SystemAlert) -> bool: ...


class PauseController(Protocol):
    async def is_system_paused(self) -> bool: ...

    async def pause_system(self, *, reason: str) -> bool: ...


class LiveReplyMonitor:
    """Scan immediately after activity and at least once every 30 seconds."""

    def __init__(
        self,
        *,
        browser: LiveBrowser,
        watcher: LiveWatcher,
        notifier: AlertNotifier | None = None,
        pause_controller: PauseController | None = None,
        signal_factory: Callable[[Any], ActivitySignal] = ReplyActivitySignal,
        fallback_seconds: float = 30,
    ) -> None:
        self._browser = browser
        self._watcher = watcher
        self._notifier = notifier
        self._pause_controller = pause_controller
        self._signal_factory = signal_factory
        self._fallback_seconds = fallback_seconds

    async def run_cycles(self, cycles: int) -> None:
        """Run a bounded number of successful scans for deterministic tests."""

        if cycles < 1:
            return
        async with self._browser.new_page() as page:
            signal = self._signal_factory(page)
            for index in range(cycles):
                await self._watcher.run(page=page)
                if index + 1 < cycles:
                    signal.clear()
                    await signal.wait(self._fallback_seconds)

    async def run_until_cancelled(self) -> None:
        """Stop reply monitoring after one serious failure and alert once."""

        while True:
            try:
                if (self._pause_controller is not None
                        and await self._pause_controller.is_system_paused()):
                    return
                async with self._browser.new_page() as page:
                    signal = self._signal_factory(page)
                    while True:
                        if (self._pause_controller is not None
                                and await self._pause_controller.is_system_paused()):
                            return
                        await self._watcher.run(page=page)
                        signal.clear()
                        await signal.wait(self._fallback_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                changed = True
                if self._pause_controller is not None:
                    changed = await self._pause_controller.pause_system(
                        reason=f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
                    )
                if self._notifier is not None and changed:
                    with suppress(Exception):
                        await self._notifier.queue_and_dispatch(SystemAlert(
                            notification_key=(
                                f"reply-monitor-error:{datetime.now(TEHRAN).date()}"
                            ),
                            title="کارلنسر: بررسی پاسخ کارفرما با مشکل روبه‌رو شد",
                            body=(
                                "پایش پاسخ‌های کارفرما ناموفق شد و سیستم خودکار "
                                "متوقف شده است. ورود و دسترسی به سایت باید بررسی شود."
                            ),
                        ))
                return
