from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from karlancer_agent.browser.replies import (
    KarlancerReplyReader,
    ReplyWatchTarget,
    parse_conversation,
)

FIXTURE = Path("tests/fixtures/reply_page.html")
TEHRAN = timezone(timedelta(hours=3, minutes=30))
OBSERVED_AT = datetime(2026, 8, 28, 10, 25, tzinfo=TEHRAN)
PROJECT_URL = (
    "https://www.karlancer.com/projects/"
    "هوش-مصنوعی-حقوقی-2m6m20w5w850"
)
CONVERSATION_URL = (
    "https://www.karlancer.com/panel/messages?room=7062838&type=messages"
)


def test_parser_finds_first_employer_message_after_matching_project_bid() -> None:
    scan = parse_conversation(
        FIXTURE.read_text(encoding="utf-8"),
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=OBSERVED_AT,
    )

    assert scan.matched_project is True
    assert scan.first_reply is not None
    assert scan.first_reply.employer_message_id == "27004106"
    assert scan.first_reply.text == "سلام، مدل کاملاً روی سرور خودمان اجرا می‌شود؟"
    assert scan.first_reply.received_at == datetime(
        2026, 8, 28, 10, 18, tzinfo=TEHRAN
    )
    assert str(scan.first_reply.conversation_url) == CONVERSATION_URL


def test_parser_ignores_own_messages_old_messages_and_later_replies() -> None:
    scan = parse_conversation(
        FIXTURE.read_text(encoding="utf-8"),
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=OBSERVED_AT,
    )

    assert scan.first_reply is not None
    assert "قدیمی" not in scan.first_reply.text
    assert "مدل محلی" not in scan.first_reply.text
    assert "قیمت دقیق" not in scan.first_reply.text


def test_parser_does_not_match_a_different_project_in_same_room() -> None:
    scan = parse_conversation(
        FIXTURE.read_text(encoding="utf-8"),
        project_id="11111111-1111-1111-1111-111111111111",
        project_url="https://www.karlancer.com/projects/different-project-123",
        conversation_url=CONVERSATION_URL,
        observed_at=OBSERVED_AT,
    )

    assert scan.matched_project is False
    assert scan.first_reply is None


def test_parser_uses_persian_date_separator_instead_of_poll_date() -> None:
    scan = parse_conversation(
        FIXTURE.read_text(encoding="utf-8"),
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=datetime(2026, 9, 9, 12, 0, tzinfo=TEHRAN),
    )

    assert scan.first_reply is not None
    assert scan.first_reply.received_at == datetime(2026, 8, 28, 10, 18, tzinfo=TEHRAN)


@pytest.mark.parametrize(
    ("separator", "expected_day"),
    (
        ("امروز", 9),
        ("دیروز", 8),
        ("شنبه", 5),
        ("پنج شنبه", 3),
    ),
)
def test_parser_resolves_relative_persian_date_separators(
    separator: str, expected_day: int
) -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace("۶ شهریور", separator)
    scan = parse_conversation(
        html,
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=datetime(2026, 9, 9, 12, 0, tzinfo=TEHRAN),
    )

    assert scan.first_reply is not None
    assert scan.first_reply.received_at.date() == datetime(
        2026, 9, expected_day, tzinfo=TEHRAN
    ).date()


class FakeRoomLocator:
    def __init__(self, page: "FakeMessagesPage", index: int | None = None) -> None:
        self.page = page
        self.index = index

    async def count(self) -> int:
        return len(self.page.rooms)

    def nth(self, index: int) -> "FakeRoomLocator":
        return FakeRoomLocator(self.page, index)

    async def click(self) -> None:
        assert self.index is not None
        self.page.events.append(("click-room", self.index))
        self.page.current = self.index
        self.page.url = (
            f"https://www.karlancer.com/panel/messages?room={7000 + self.index}"
            "&type=messages"
        )


class FakeMessagesPage:
    url = "about:blank"

    def __init__(self, rooms: list[str]) -> None:
        self.rooms = rooms
        self.current: int | None = None
        self.events: list[tuple] = []

    async def goto(self, url: str, **kwargs: object) -> None:
        self.url = url
        self.events.append(("goto", url))

    def locator(self, selector: str) -> FakeRoomLocator:
        assert selector == ".room-item"
        return FakeRoomLocator(self)

    async def wait_for_selector(self, selector: str, **kwargs: object) -> None:
        self.events.append(("wait", selector))

    async def content(self) -> str:
        assert self.current is not None
        return self.rooms[self.current]


@pytest.mark.asyncio
async def test_reader_scans_rooms_read_only_until_project_conversation_is_found() -> None:
    unrelated = FIXTURE.read_text(encoding="utf-8").replace(
        "2m6m20w5w850", "different-project"
    )
    page = FakeMessagesPage([unrelated, FIXTURE.read_text(encoding="utf-8")])
    target = ReplyWatchTarget(
        project_id="11111111-1111-1111-1111-111111111111",
        title="هوش مصنوعی حقوقی",
        project_url=PROJECT_URL,
        project_summary="ساخت دستیار حقوقی محلی",
        submitted_at=OBSERVED_AT,
    )

    scan = await KarlancerReplyReader(
        clock=lambda: OBSERVED_AT, max_rooms=10
    ).observe(page, target)

    assert scan.matched_project is True
    assert scan.first_reply is not None
    assert str(scan.conversation_url).endswith("room=7001&type=messages")
    assert page.events[:3] == [
        ("goto", "https://www.karlancer.com/panel/messages"),
        ("click-room", 0),
        ("wait", "#messageBox app-room-messages:not(.d-none)"),
    ]
    assert not any(event[0] in {"fill", "type", "send"} for event in page.events)
