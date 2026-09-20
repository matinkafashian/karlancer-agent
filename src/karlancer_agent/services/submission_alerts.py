"""Durable, deduplicated Telegram alerts for submission quota failures."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from karlancer_agent.notifications.telegram import (
    TEHRAN,
    SystemReport,
    TelegramReceipt,
)

SubmissionLimitReason = Literal["daily_limit_reached", "platform_submission_limit"]


class SubmissionLimitRepository(Protocol):
    async def queue_submission_limit_alert(
        self,
        project_id: UUID,
        *,
        notification_key: str,
        title: str,
        project_url: str,
        reason: SubmissionLimitReason,
        observed_at: datetime,
    ) -> bool: ...

    async def submission_limit_report_data(
        self, notification_key: str
    ) -> dict[str, Any] | None: ...

    async def mark_notification_delivered(
        self, notification_key: str, *, delivered_at: datetime
    ) -> bool: ...


class SystemReporter(Protocol):
    async def send_system_report(
        self, report: SystemReport, *, notification_key: str
    ) -> TelegramReceipt: ...


class SubmissionLimitAlertService:
    """Queue and deliver one alert per reason per Tehran calendar day."""

    def __init__(
        self,
        *,
        repository: SubmissionLimitRepository,
        telegram: SystemReporter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._telegram = telegram
        self._clock = clock

    async def notify(
        self,
        *,
        project_id: UUID,
        title: str,
        project_url: str,
        reason: SubmissionLimitReason,
        observed_at: datetime,
    ) -> bool:
        day = observed_at.astimezone(TEHRAN).date().isoformat()
        key = f"submission-limit:{reason}:{day}"
        await self._repository.queue_submission_limit_alert(
            project_id,
            notification_key=key,
            title=title,
            project_url=project_url,
            reason=reason,
            observed_at=observed_at,
        )
        data = await self._repository.submission_limit_report_data(key)
        if data is None:
            raise RuntimeError("submission-limit alert was not durably queued")
        if bool(data["delivered"]):
            return False

        report = SystemReport(
            title="توقف ارسال پیشنهاد در کارلنسر",
            body=_report_body(data),
        )
        await self._telegram.send_system_report(report, notification_key=key)
        completed = await self._repository.mark_notification_delivered(
            key,
            delivered_at=self._clock(),
        )
        if not completed:
            raise RuntimeError("submission-limit alert delivery could not be persisted")
        return True


def _report_body(data: dict[str, Any]) -> str:
    reason = cast(SubmissionLimitReason, data["reason"])
    reason_text = {
        "daily_limit_reached": "سقف ایمنی روزانه ربات پر شده است.",
        "platform_submission_limit": (
            "سهمیه یا پلن کارلنسر اجازه ارسال پیشنهاد جدید نمی‌دهد."
        ),
    }[reason]
    observed_at = cast(datetime, data["observed_at"]).astimezone(TEHRAN)
    return (
        f"{reason_text}\n"
        f"پروژه: {cast(str, data['title'])}\n"
        f"زمان: {observed_at:%Y-%m-%d %H:%M}\n"
        f"لینک: {cast(str, data['project_url'])}"
    )
