"""Durable delivery of small operational Telegram alerts."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.notifications.telegram import (
    TEHRAN,
    SystemReport,
    TelegramReceipt,
)
from karlancer_agent.services.outbox import OutboxDispatcher


class SystemAlert(BaseModel):
    """A stable, deduplicated owner-only operational event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    notification_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)


class OperationalAlertRepository(Protocol):
    async def queue_system_alert(
        self, *, notification_key: str, title: str, body: str
    ) -> bool: ...

    async def system_alert_data(
        self, notification_key: str
    ) -> dict[str, Any] | None: ...

    async def mark_notification_delivered(
        self, notification_key: str, *, delivered_at: datetime
    ) -> bool: ...


class SystemReporter(Protocol):
    async def send_system_report(
        self, report: SystemReport, *, notification_key: str
    ) -> TelegramReceipt: ...


class OperationalNotifier:
    """Queue first, then deliver once; failed delivery remains retryable."""

    def __init__(
        self,
        *,
        repository: OperationalAlertRepository,
        telegram: SystemReporter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        dispatch: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._repository = repository
        self._telegram = telegram
        self._clock = clock
        self._dispatch = dispatch
        if dispatch is None and isinstance(repository, ProjectRepository):
            self._dispatch = OutboxDispatcher(
                repository=repository, telegram=telegram, clock=clock).dispatch

    async def queue_and_dispatch(self, alert: SystemAlert) -> bool:
        if alert.notification_key.startswith("n8n-auto-error-"):
            alert = SystemAlert(
                notification_key=f"n8n-error:{self._clock().astimezone(TEHRAN).date()}",
                title="کارلنسر: اجرای خودکار کامل نشد",
                body=("اجرای خودکار کامل نشد و کل اتوماسیون متوقف شد. "
                      "نتیجه ارسال احتمالی باید بررسی شود. "
                      "ادامه فقط پس از بررسی و فعال‌سازی دستی است."),
            )
        await self._repository.queue_system_alert(
            notification_key=alert.notification_key,
            title=alert.title,
            body=alert.body,
        )
        if self._dispatch is not None:
            return await self._dispatch(alert.notification_key)
        data = await self._repository.system_alert_data(alert.notification_key)
        if data is None:
            raise RuntimeError("operational alert was not durably queued")
        if bool(data["delivered"]):
            return False

        report = SystemReport(title=str(data["title"]), body=str(data["body"]))
        await self._telegram.send_system_report(
            report,
            notification_key=alert.notification_key,
        )
        completed = await self._repository.mark_notification_delivered(
            alert.notification_key,
            delivered_at=self._clock(),
        )
        if not completed:
            raise RuntimeError("operational alert delivery could not be persisted")
        return True
