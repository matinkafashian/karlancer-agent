from contextlib import asynccontextmanager

import pytest

from karlancer_agent.services.live_reply_monitor import LiveReplyMonitor
from karlancer_agent.services.reply_watcher import ReplyWatchRunResult


class FakeBrowser:
    def __init__(self) -> None:
        self.page = object()

    @asynccontextmanager
    async def new_page(self):
        yield self.page


class FakeWatcher:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *, page):
        self.calls += 1
        return ReplyWatchRunResult(projects_checked=1, notifications_sent=0)


class FakeSignal:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.wait_calls: list[float] = []
        self.clear_calls = 0

    def clear(self) -> None:
        self.clear_calls += 1

    async def wait(self, timeout_seconds: float):
        self.wait_calls.append(timeout_seconds)
        return self.events.pop(0)


@pytest.mark.asyncio
async def test_live_monitor_scans_immediately_after_message_event() -> None:
    browser = FakeBrowser()
    watcher = FakeWatcher()
    signal = FakeSignal(["event"])
    monitor = LiveReplyMonitor(
        browser=browser,
        watcher=watcher,
        signal_factory=lambda page: signal,
    )

    await monitor.run_cycles(2)

    assert watcher.calls == 2
    assert signal.wait_calls == [30]
    assert signal.clear_calls == 1


@pytest.mark.asyncio
async def test_live_monitor_uses_thirty_second_fallback_without_busy_loop() -> None:
    browser = FakeBrowser()
    watcher = FakeWatcher()
    signal = FakeSignal(["timeout"])
    monitor = LiveReplyMonitor(
        browser=browser,
        watcher=watcher,
        signal_factory=lambda page: signal,
    )

    await monitor.run_cycles(2)

    assert watcher.calls == 2
    assert signal.wait_calls == [30]


class FailingWatcher:
    async def run(self, *, page):
        raise RuntimeError("transport failed")


class FakeNotifier:
    def __init__(self) -> None:
        self.alerts = []

    async def queue_and_dispatch(self, alert) -> bool:
        self.alerts.append(alert)
        return True


class FakePauseController:
    def __init__(self) -> None:
        self.reasons = []

    async def is_system_paused(self) -> bool:
        return bool(self.reasons)

    async def pause_system(self, *, reason: str) -> bool:
        self.reasons.append(reason)
        return True


@pytest.mark.asyncio
async def test_transport_failure_stops_and_alerts_once_without_retry() -> None:
    delays: list[float] = []
    notifier = FakeNotifier()
    pause = FakePauseController()

    monitor = LiveReplyMonitor(
        browser=FakeBrowser(),
        watcher=FailingWatcher(),
        notifier=notifier,
        pause_controller=pause,
        signal_factory=lambda page: FakeSignal([]),
    )

    await monitor.run_until_cancelled()

    assert delays == []
    assert len(notifier.alerts) >= 1
    assert len({alert.notification_key for alert in notifier.alerts}) == 1
    assert "پاسخ" in notifier.alerts[0].body
    assert pause.reasons == ["RuntimeError: transport failed"]


@pytest.mark.asyncio
async def test_existing_global_pause_prevents_browser_and_monitor_calls() -> None:
    pause = FakePauseController()
    pause.reasons.append("submission failed")
    watcher = FakeWatcher()
    monitor = LiveReplyMonitor(
        browser=FakeBrowser(), watcher=watcher, pause_controller=pause,
        signal_factory=lambda page: (_ for _ in ()).throw(AssertionError("browser opened")),
    )
    await monitor.run_until_cancelled()
    assert watcher.calls == 0
