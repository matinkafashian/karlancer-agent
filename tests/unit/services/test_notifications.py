from datetime import UTC, datetime
from typing import Any

import pytest

from karlancer_agent.notifications.telegram import SystemReport, TelegramReceipt
from karlancer_agent.services.notifications import OperationalNotifier, SystemAlert

NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}

    async def queue_system_alert(
        self, *, notification_key: str, title: str, body: str
    ) -> bool:
        if notification_key in self.events:
            return False
        self.events[notification_key] = {
            "title": title,
            "body": body,
            "delivered": False,
        }
        return True

    async def system_alert_data(self, notification_key: str) -> dict[str, Any] | None:
        return self.events.get(notification_key)

    async def mark_notification_delivered(
        self, notification_key: str, *, delivered_at: datetime
    ) -> bool:
        event = self.events[notification_key]
        if event["delivered"]:
            return False
        event["delivered"] = True
        event["delivered_at"] = delivered_at
        return True


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[SystemReport, str]] = []

    async def send_system_report(
        self, report: SystemReport, *, notification_key: str
    ) -> TelegramReceipt:
        self.calls.append((report, notification_key))
        return TelegramReceipt(message_id=42, notification_key=notification_key)


@pytest.mark.asyncio
async def test_operational_alert_is_durable_and_delivered_once() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    notifier = OperationalNotifier(
        repository=repository,
        telegram=telegram,
        clock=lambda: NOW,
    )
    alert = SystemAlert(
        notification_key="provider-quota:one",
        title="توقف موقت هوش مصنوعی",
        body="سهمیه موقتاً تمام شده است.",
    )

    assert await notifier.queue_and_dispatch(alert) is True
    assert await notifier.queue_and_dispatch(alert) is False
    assert len(telegram.calls) == 1
    assert repository.events[alert.notification_key]["delivered_at"] == NOW


@pytest.mark.asyncio
async def test_n8n_errors_are_plain_and_deduplicated_across_execution_ids():
    telegram = FakeTelegram()
    notifier = OperationalNotifier(
        repository=FakeRepository(), telegram=telegram, clock=lambda: NOW)
    for execution in (26, 27):
        await notifier.queue_and_dispatch(SystemAlert(
            notification_key=f"n8n-auto-error-{execution}", title="error", body="raw stack trace",
        ))
    assert len(telegram.calls) == 1
    assert "raw stack" not in telegram.calls[0][0].body
    assert "بررسی" in telegram.calls[0][0].body
