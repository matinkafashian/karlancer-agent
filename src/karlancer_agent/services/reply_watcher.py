"""First-reply notification and terminal human handoff."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from karlancer_agent.browser.replies import ConversationScan, ReplyWatchTarget
from karlancer_agent.notifications.telegram import FirstReplyReport, TelegramReceipt


class ReplyReader(Protocol):
    async def observe(self, page: object, target: ReplyWatchTarget) -> ConversationScan: ...


class ReplyRepository(Protocol):
    async def claim_reply_watch_targets(self) -> tuple[dict[str, Any], ...]: ...

    async def set_submission_conversation_url(
        self, project_id: UUID, *, conversation_url: str
    ) -> bool: ...

    async def queue_first_reply_once(
        self, reply: object, *, notification_key: str
    ) -> bool: ...

    async def pending_first_reply_notification_keys(self) -> tuple[str, ...]: ...

    async def first_reply_report_data(self, notification_key: str) -> dict[str, Any] | None: ...

    async def complete_first_reply_notification(
        self, notification_key: str, *, notified_at: datetime
    ) -> bool: ...


class FirstReplyReporter(Protocol):
    async def send_first_reply_report(
        self, report: FirstReplyReport, *, notification_key: str
    ) -> TelegramReceipt: ...


class ReplyWatchRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projects_checked: int
    notifications_sent: int


class ReplyWatcher:
    def __init__(
        self,
        *,
        repository: ReplyRepository,
        reader: ReplyReader,
        telegram: FirstReplyReporter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._reader = reader
        self._telegram = telegram
        self._clock = clock

    async def run(self, *, page: object) -> ReplyWatchRunResult:
        sent = 0
        for key in await self._repository.pending_first_reply_notification_keys():
            sent += await self._dispatch(key)

        raw_targets = await self._repository.claim_reply_watch_targets()
        targets = tuple(ReplyWatchTarget.model_validate(value) for value in raw_targets)
        for target in targets:
            observation = await self._reader.observe(page, target)
            if not observation.matched_project:
                continue
            observed_url = str(observation.conversation_url)
            if target.conversation_url is None or str(target.conversation_url) != observed_url:
                await self._repository.set_submission_conversation_url(
                    target.project_id, conversation_url=observed_url
                )
            if observation.first_reply is None:
                continue
            key = (
                f"first-reply:{target.project_id}:"
                f"{observation.first_reply.employer_message_id}"
            )
            queued = await self._repository.queue_first_reply_once(
                observation.first_reply,
                notification_key=key,
            )
            if queued:
                sent += await self._dispatch(key)
        return ReplyWatchRunResult(
            projects_checked=len(targets),
            notifications_sent=sent,
        )

    async def _dispatch(self, notification_key: str) -> int:
        data = await self._repository.first_reply_report_data(notification_key)
        if data is None or bool(data["delivered"]):
            return 0
        report = FirstReplyReport(
            title=cast(str, data["title"]),
            project_summary=cast(str, data["project_summary"]),
            reply_summary=cast(str, data["reply_summary"]),
            conversation_url=cast(str, data["conversation_url"]),
        )
        await self._telegram.send_first_reply_report(
            report,
            notification_key=notification_key,
        )
        completed = await self._repository.complete_first_reply_notification(
            notification_key,
            notified_at=self._clock(),
        )
        if not completed:
            raise RuntimeError("first-reply handoff could not be persisted")
        return 1
