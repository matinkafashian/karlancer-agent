"""Bounded durable Telegram delivery, including while site/model work is paused."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.notifications.telegram import FirstReplyReport, SystemReport, TelegramReceipt

logger = logging.getLogger(__name__)


class SystemSender(Protocol):
    async def send_system_report(
        self, report: SystemReport, *, notification_key: str
    ) -> TelegramReceipt: ...


class ReplySender(Protocol):
    async def send_first_reply_report(
        self, report: FirstReplyReport, *, notification_key: str
    ) -> TelegramReceipt: ...


class OutboxDispatcher:
    def __init__(
        self,
        *,
        repository: ProjectRepository,
        telegram: SystemSender | ReplySender,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._telegram = telegram
        self._clock = clock

    async def dispatch(self, key: str) -> bool:
        data = await self._repository.system_alert_data(key)
        is_reply = data is None
        if is_reply:
            data = await self._repository.first_reply_report_data(key)
        if data is None or data["delivered"]:
            return False
        attempt = await self._repository.claim_notification(key, now=self._clock())
        if attempt is None:
            return False
        try:
            if is_reply:
                receipt = await cast(ReplySender, self._telegram).send_first_reply_report(
                    FirstReplyReport(
                        title=data["title"],
                        project_summary=data["project_summary"],
                        reply_summary=data["reply_summary"],
                        received_at=data["received_at"],
                        project_url=data["project_url"],
                        conversation_url=data["conversation_url"],
                    ),
                    notification_key=key,
                )
            else:
                receipt = await cast(SystemSender, self._telegram).send_system_report(
                    SystemReport(title=data["title"], body=data["body"]), notification_key=key
                )
        except Exception:
            await self._repository.finish_notification_attempt(
                key, attempt=attempt, now=self._clock(), message_id=None
            )
            changed = await self._repository.pause_system(
                reason="Telegram delivery failed; bounded outbox retry pending"
            )
            if changed:
                await self._repository.queue_system_alert(
                    notification_key=f"delivery-failure:{hashlib.sha256(key.encode()).hexdigest()}",
                    title="کارلنسر: تحویل اعلان ناموفق بود",
                    body=(
                        "فعالیت خودکار متوقف شد. تحویل اعلان حداکثر سه بار تلاش می‌شود؛ "
                        "پس از رفع مشکل، ادامهٔ کار نیازمند بررسی دستی است."
                    ),
                )
            # No raw exception: HTTP failures may contain bot tokens or report text.
            logger.warning("Telegram delivery attempt failed (%s/3)", attempt)
            return False
        return await self._repository.finish_notification_attempt(
            key, attempt=attempt, now=self._clock(), message_id=receipt.message_id
        )

    async def run_once(self) -> int:
        sent = 0
        for key in await self._repository.pending_delivery_keys(now=self._clock()):
            sent += int(await self.dispatch(key))
        return sent

    async def run_until_cancelled(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.error("Outbox recovery failed; stopping delivery worker")
                await self._repository.pause_system(reason="outbox recovery persistence failure")
                return
            await asyncio.sleep(30)
