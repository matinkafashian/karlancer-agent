"""First-reply notification and terminal human handoff."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from karlancer_agent.browser.replies import ConversationScan, ReplyPage, ReplyWatchTarget
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.models import FirstReply
from karlancer_agent.notifications.telegram import FirstReplyReport, TelegramReceipt
from karlancer_agent.services.outbox import OutboxDispatcher


class ReplyReader(Protocol):
    async def observe(self, page: ReplyPage, target: ReplyWatchTarget) -> ConversationScan: ...


class ReplyRepository(Protocol):
    async def claim_reply_watch_targets(self) -> tuple[dict[str, Any], ...]: ...

    async def set_submission_conversation_url(
        self, project_id: UUID, *, conversation_url: str
    ) -> bool: ...

    async def confirm_submission_from_conversation(
        self, project_id: UUID, *, conversation_url: str
    ) -> bool: ...

    async def queue_first_reply_once(
        self, reply: FirstReply, *, notification_key: str
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


class PauseReader(Protocol):
    async def is_system_paused(self) -> bool: ...


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
        pause_controller: PauseReader | None = None,
        dispatch: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._repository = repository
        self._reader = reader
        self._telegram = telegram
        self._clock = clock
        self._durable_dispatch = dispatch
        if dispatch is None and isinstance(repository, ProjectRepository):
            self._durable_dispatch = OutboxDispatcher(
                repository=repository, telegram=telegram, clock=clock).dispatch
        self._pause_controller = pause_controller
        self._lock = asyncio.Lock()

    async def run(self, *, page: ReplyPage) -> ReplyWatchRunResult:
        async with self._lock:
            return await self._run(page=page)

    async def _paused(self) -> bool:
        return (
            self._pause_controller is not None
            and await self._pause_controller.is_system_paused()
        )

    async def _run(self, *, page: ReplyPage) -> ReplyWatchRunResult:
        sent = 0
        if await self._paused():
            return ReplyWatchRunResult(projects_checked=0, notifications_sent=0)
        for key in await self._repository.pending_first_reply_notification_keys():
            if await self._paused():
                return ReplyWatchRunResult(projects_checked=0, notifications_sent=sent)
            sent += await self._dispatch(key)

        raw_targets = await self._repository.claim_reply_watch_targets()
        begin_scan = getattr(self._reader, "begin_scan", None)
        if begin_scan is not None:
            begin_scan()
        targets = tuple(ReplyWatchTarget.model_validate(value) for value in raw_targets)
        for target in targets:
            if await self._paused():
                break
            observation = await self._reader.observe(page, target)
            if not observation.matched_project:
                continue
            observed_url = str(observation.conversation_url)
            if target.requires_reconciliation:
                confirmed = await self._repository.confirm_submission_from_conversation(
                    target.project_id, conversation_url=observed_url
                )
                if not confirmed:
                    continue
            elif target.conversation_url is None or str(target.conversation_url) != observed_url:
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
        if await self._paused():
            return 0
        if self._durable_dispatch is not None:
            return int(await self._durable_dispatch(notification_key))
        data = await self._repository.first_reply_report_data(notification_key)
        if data is None or bool(data["delivered"]):
            return 0
        report = FirstReplyReport(
            title=cast(str, data["title"]),
            project_summary=cast(str, data["project_summary"]),
            reply_summary=cast(str, data["reply_summary"]),
            received_at=cast(datetime, data["received_at"]),
            project_url=cast(str, data["project_url"]),
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
