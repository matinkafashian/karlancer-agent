from datetime import UTC, datetime
from uuid import UUID

import pytest

from karlancer_agent.browser.replies import ConversationScan
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.domain.models import FirstReply
from karlancer_agent.notifications.telegram import TelegramReceipt
from karlancer_agent.services.reply_watcher import ReplyWatcher, ReplyWatchTarget

PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
CONVERSATION_URL = "https://www.karlancer.com/panel/messages?room=7062838&type=messages"


class FakeRepository:
    def __init__(self) -> None:
        self.state = ProjectState.SUBMITTED
        self.queued = False
        self.delivered = False
        self.conversation_url: str | None = None
        self.reconciled = False

    async def claim_reply_watch_targets(self):
        if self.state in {ProjectState.REPLY_NOTIFIED, ProjectState.HUMAN_HANDOFF}:
            return ()
        if self.state is ProjectState.SUBMITTED:
            self.state = ProjectState.REPLY_WATCH
        return (
            {
                "project_id": PROJECT_ID,
                "title": "هوش مصنوعی حقوقی",
                "project_url": "https://www.karlancer.com/projects/هوش-مصنوعی-حقوقی-2m6m20w5w850",
                "project_summary": "ساخت دستیار حقوقی محلی",
                "submitted_at": datetime(2026, 8, 28, 10, 0, tzinfo=UTC),
                "conversation_url": self.conversation_url,
                "requires_reconciliation": True,
            },
        )

    async def set_submission_conversation_url(self, project_id, *, conversation_url):
        assert project_id == PROJECT_ID
        self.conversation_url = conversation_url
        return True

    async def confirm_submission_from_conversation(self, project_id, *, conversation_url):
        self.reconciled = True
        self.conversation_url = conversation_url
        return True

    async def queue_first_reply_once(self, reply, *, notification_key):
        if self.queued:
            return False
        self.queued = True
        self.state = ProjectState.REPLY_NOTIFIED
        self.reply = reply
        self.notification_key = notification_key
        return True

    async def pending_first_reply_notification_keys(self):
        if self.queued and not self.delivered:
            return (self.notification_key,)
        return ()

    async def first_reply_report_data(self, notification_key):
        if not self.queued or notification_key != self.notification_key:
            return None
        return {
            "project_id": PROJECT_ID,
            "title": "هوش مصنوعی حقوقی",
            "project_summary": "ساخت دستیار حقوقی محلی",
            "project_url": "https://www.karlancer.com/projects/هوش-مصنوعی-حقوقی-2m6m20w5w850",
            "reply_summary": self.reply.text,
            "received_at": self.reply.received_at,
            "conversation_url": CONVERSATION_URL,
            "delivered": self.delivered,
        }

    async def complete_first_reply_notification(self, notification_key, *, notified_at):
        assert notification_key == self.notification_key
        self.delivered = True
        self.state = ProjectState.HUMAN_HANDOFF
        return True


class FakeReader:
    async def observe(self, page, target: ReplyWatchTarget):
        return ConversationScan(
            matched_project=True,
            conversation_url=CONVERSATION_URL,
            first_reply=FirstReply(
                project_id=str(target.project_id),
                employer_message_id="27004106",
                text="سلام، مدل کاملاً روی سرور خودمان اجرا می‌شود؟",
                received_at=datetime(2026, 8, 28, 6, 48, tzinfo=UTC),
                conversation_url=CONVERSATION_URL,
            ),
        )


class FakeTelegram:
    def __init__(self) -> None:
        self.reports = []

    async def send_first_reply_report(self, report, *, notification_key):
        self.reports.append((report, notification_key))
        return TelegramReceipt(message_id=88, notification_key=notification_key)


@pytest.mark.asyncio
async def test_first_employer_reply_notifies_once_then_terminally_hands_off() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    watcher = ReplyWatcher(
        repository=repository,
        reader=FakeReader(),
        telegram=telegram,
        clock=lambda: datetime(2026, 8, 28, 7, 0, tzinfo=UTC),
    )

    first = await watcher.run(page=object())
    second = await watcher.run(page=object())

    assert first.notifications_sent == 1
    assert second.notifications_sent == 0
    assert repository.state is ProjectState.HUMAN_HANDOFF
    assert len(telegram.reports) == 1
    assert repository.reconciled is True
    report, _ = telegram.reports[0]
    assert report.title == "هوش مصنوعی حقوقی"
    assert report.project_summary == "ساخت دستیار حقوقی محلی"
    assert str(report.project_url).startswith("https://www.karlancer.com/projects/")
    assert "مدل کاملاً روی سرور" in report.reply_summary
    assert report.conversation_url == CONVERSATION_URL
