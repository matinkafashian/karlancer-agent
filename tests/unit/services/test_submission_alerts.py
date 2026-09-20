from datetime import UTC, datetime
from uuid import UUID

import pytest

from karlancer_agent.notifications.telegram import TelegramReceipt
from karlancer_agent.services.submission_alerts import SubmissionLimitAlertService

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
OBSERVED_AT = datetime(2026, 9, 9, 21, 45, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, object]] = {}

    async def queue_submission_limit_alert(self, project_id, **kwargs):
        key = kwargs["notification_key"]
        if key in self.events:
            return False
        self.events[key] = {
            "project_id": project_id,
            "title": kwargs["title"],
            "project_url": kwargs["project_url"],
            "reason": kwargs["reason"],
            "observed_at": kwargs["observed_at"],
            "delivered": False,
        }
        return True

    async def submission_limit_report_data(self, notification_key):
        return self.events.get(notification_key)

    async def mark_notification_delivered(self, notification_key, *, delivered_at):
        event = self.events[notification_key]
        if event["delivered"]:
            return False
        event["delivered"] = True
        event["delivered_at"] = delivered_at
        return True


class FakeTelegram:
    def __init__(self) -> None:
        self.reports = []

    async def send_system_report(self, report, *, notification_key):
        self.reports.append((report, notification_key))
        return TelegramReceipt(message_id=51, notification_key=notification_key)


@pytest.mark.asyncio
async def test_same_limit_is_reported_only_once_per_tehran_day() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    service = SubmissionLimitAlertService(
        repository=repository,
        telegram=telegram,
        clock=lambda: OBSERVED_AT,
    )

    first = await service.notify(
        project_id=PROJECT_ID,
        title="ساخت ربات تلگرام",
        project_url="https://www.karlancer.com/projects/322263/example",
        reason="platform_submission_limit",
        observed_at=OBSERVED_AT,
    )
    second = await service.notify(
        project_id=PROJECT_ID,
        title="پروژه دوم",
        project_url="https://www.karlancer.com/projects/322264/example",
        reason="platform_submission_limit",
        observed_at=OBSERVED_AT,
    )

    assert first is True
    assert second is False
    assert len(telegram.reports) == 1
    report, notification_key = telegram.reports[0]
    assert notification_key == "submission-limit:platform_submission_limit:2026-09-10"
    assert report.title == "توقف ارسال پیشنهاد در کارلنسر"
    assert "سهمیه یا پلن کارلنسر" in report.body
    assert "ساخت ربات تلگرام" in report.body
    assert "https://www.karlancer.com/projects/322263/example" in report.body


@pytest.mark.asyncio
async def test_internal_daily_limit_has_clear_reason() -> None:
    telegram = FakeTelegram()
    service = SubmissionLimitAlertService(
        repository=FakeRepository(),
        telegram=telegram,
        clock=lambda: OBSERVED_AT,
    )

    await service.notify(
        project_id=PROJECT_ID,
        title="پردازش تصویر",
        project_url="https://www.karlancer.com/projects/322265/example",
        reason="daily_limit_reached",
        observed_at=OBSERVED_AT,
    )

    report, _ = telegram.reports[0]
    assert "سقف ایمنی روزانه ربات" in report.body
