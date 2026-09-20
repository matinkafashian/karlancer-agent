from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from karlancer_agent.browser.replies import (
    KarlancerReplyReader,
    ReplyActivitySignal,
    ReplyWatchTarget,
    parse_conversation,
)

FIXTURE = Path("tests/fixtures/reply_page.html")
TEHRAN = timezone(timedelta(hours=3, minutes=30))
OBSERVED_AT = datetime(2026, 8, 28, 10, 25, tzinfo=TEHRAN)
PROJECT_URL = "https://www.karlancer.com/projects/هوش-مصنوعی-حقوقی-2m6m20w5w850"
CONVERSATION_URL = "https://www.karlancer.com/panel/messages?room=7062838&type=messages"


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
    assert scan.first_reply.received_at == datetime(2026, 8, 28, 10, 18, tzinfo=TEHRAN)
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


def test_parser_matches_relative_project_links_from_live_message_page() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace(
        "https://www.karlancer.com/projects/", "/projects/"
    )
    scan = parse_conversation(
        html,
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=OBSERVED_AT,
    )

    assert scan.matched_project is True
    assert scan.first_reply is not None


def test_parser_matches_stable_project_id_when_persian_slug_changed() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace(
        "هوش-مصنوعی-حقوقی-2m6m20w5w850",
        "عنوان-ویرایش-شده-2m6m20w5w850",
    )
    scan = parse_conversation(
        html,
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=OBSERVED_AT,
    )

    assert scan.matched_project is True


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
    assert (
        scan.first_reply.received_at.date() == datetime(2026, 9, expected_day, tzinfo=TEHRAN).date()
    )


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
            f"https://www.karlancer.com/panel/messages?room={7000 + self.index}&type=messages"
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

    async def wait_for_timeout(self, timeout: float) -> None:
        self.events.append(("wait-timeout", timeout))

    async def content(self) -> str:
        assert self.current is not None
        return self.rooms[self.current]


@pytest.mark.asyncio
async def test_reader_scans_rooms_read_only_until_project_conversation_is_found() -> None:
    unrelated = FIXTURE.read_text(encoding="utf-8").replace("2m6m20w5w850", "different-project")
    page = FakeMessagesPage([unrelated, FIXTURE.read_text(encoding="utf-8")])
    target = ReplyWatchTarget(
        project_id="11111111-1111-1111-1111-111111111111",
        title="هوش مصنوعی حقوقی",
        project_url=PROJECT_URL,
        project_summary="ساخت دستیار حقوقی محلی",
        submitted_at=OBSERVED_AT,
    )

    scan = await KarlancerReplyReader(clock=lambda: OBSERVED_AT, max_rooms=10).observe(page, target)

    assert scan.matched_project is True
    assert scan.first_reply is not None
    assert str(scan.conversation_url).endswith("room=7001&type=messages")
    assert page.events[:4] == [
        ("goto", "https://www.karlancer.com/panel/messages"),
        ("wait", ".room-item"),
        ("click-room", 0),
        ("wait", "#messageBox app-room-messages:not(.d-none)"),
    ]
    assert not any(event[0] in {"fill", "type", "send"} for event in page.events)
    before = list(page.events)
    same = target.model_copy(update={"conversation_url": scan.conversation_url})
    repeated = await KarlancerReplyReader(clock=lambda: OBSERVED_AT).observe(page, same)
    assert repeated.first_reply is not None
    assert ("goto", str(scan.conversation_url)) in page.events

    # One reader shares room snapshots across every target in a scan.
    reader = KarlancerReplyReader(clock=lambda: OBSERVED_AT)
    await reader.observe(page, target)
    before = list(page.events)
    await reader.observe(page, same)
    assert page.events == before


class FakeEventPage(FakeMessagesPage):
    def __init__(self) -> None:
        super().__init__([])
        self.handlers = {}

    def on(self, event, handler) -> None:
        self.handlers[event] = handler


class FakeWebSocket:
    url = "wss://www.karlancer.com/panel/messages"

    def __init__(self) -> None:
        self.handlers = {}

    def on(self, event, handler) -> None:
        self.handlers[event] = handler


@pytest.mark.asyncio
async def test_activity_signal_wakes_on_message_response_and_websocket_frame() -> None:
    page = FakeEventPage()
    signal = ReplyActivitySignal(page)

    page.handlers["response"](
        type("Response", (), {"url": "https://www.karlancer.com/panel/messages?room=1"})()
    )
    assert await signal.wait(0.01) == "event"

    signal.clear()
    websocket = FakeWebSocket()
    page.handlers["websocket"](websocket)
    websocket.handlers["framereceived"]("new-message")
    assert await signal.wait(0.01) == "event"


def _parse_html(html: str, observed_at=OBSERVED_AT):
    return parse_conversation(
        html,
        project_id="11111111-1111-1111-1111-111111111111",
        project_url=PROJECT_URL,
        conversation_url=CONVERSATION_URL,
        observed_at=observed_at,
    )


def test_parser_interprets_site_clock_in_tehran_when_poll_clock_is_utc():
    scan = _parse_html(FIXTURE.read_text(encoding="utf-8"), OBSERVED_AT.astimezone(UTC))
    assert scan.first_reply.received_at == datetime(2026, 8, 28, 6, 48, tzinfo=UTC)
    assert scan.first_reply.received_at.tzinfo == UTC


def test_parser_uses_tehran_day_at_utc_midnight_boundary():
    html = (
        FIXTURE.read_text(encoding="utf-8").replace("۶ شهریور", "امروز").replace("۱۰:۱۸", "۰۰:۱۸")  # noqa: RUF001 - Persian site clock
    )
    scan = _parse_html(html, datetime(2026, 8, 28, 21, 0, tzinfo=UTC))
    assert scan.first_reply.received_at == datetime(2026, 8, 28, 20, 48, tzinfo=UTC)


@pytest.mark.parametrize(
    "hidden", ['class="d-none"', "hidden", 'aria-hidden="true"', 'style="display: none"']
)
def test_parser_ignores_hidden_room_panels(hidden):
    html = FIXTURE.read_text(encoding="utf-8").replace(
        "<app-room-messages>", f"<app-room-messages {hidden}>"
    )
    assert not _parse_html(html).matched_project


def test_parser_stops_at_next_project_bid():
    html = FIXTURE.read_text(encoding="utf-8").replace(
        '<div class="date-separator">',
        '<div id="999"><a href="/projects/another-abcdefgh1234">Other bid</a></div>'
        '<div class="date-separator">',
    )
    scan = _parse_html(html)
    assert scan.matched_project
    assert scan.first_reply is None


def test_parser_does_not_read_reply_from_other_panel():
    html = FIXTURE.read_text(encoding="utf-8").replace(
        '<div class="date-separator">',
        '</app-room-messages><app-room-messages class="d-none"><div class="date-separator">',
    )
    scan = _parse_html(html)
    assert scan.matched_project
    assert scan.first_reply is None


@pytest.mark.asyncio
async def test_reader_uses_stored_room_without_listing_rooms():
    page = FakeMessagesPage([FIXTURE.read_text(encoding="utf-8")])
    page.current = 0
    target = ReplyWatchTarget(
        project_id="11111111-1111-1111-1111-111111111111",
        title="test",
        project_url=PROJECT_URL,
        project_summary="test",
        submitted_at=OBSERVED_AT,
        conversation_url=CONVERSATION_URL,
    )
    scan = await KarlancerReplyReader(clock=lambda: OBSERVED_AT).observe(page, target)
    assert scan.first_reply is not None
    assert page.events[0] == ("goto", CONVERSATION_URL)
    assert not any(event[0] == "click-room" for event in page.events)


@pytest.mark.asyncio
async def test_snapshot_rejects_wrong_room_and_stale_content():
    reader = KarlancerReplyReader()
    html = FIXTURE.read_text(encoding="utf-8")
    page = FakeMessagesPage([html])
    page.current = 0
    page.url = CONVERSATION_URL
    assert await reader._snapshot(page, expected_room="999") is None
    previous = (CONVERSATION_URL.replace("7062838", "999"), html)
    assert await reader._snapshot(page, previous=previous) is None
    assert len([event for event in page.events if event[0] == "wait-timeout"]) == 16


@pytest.mark.asyncio
async def test_snapshot_waits_for_content_transition_before_accepting_room():
    html = FIXTURE.read_text(encoding="utf-8")
    stale = html.replace("27004106", "12345678")

    class TransitionPage(FakeMessagesPage):
        async def content(self):
            if self.reads:
                self.reads -= 1
                return stale
            return html

    page = TransitionPage([html])
    page.current = 0
    page.reads = 3
    page.url = CONVERSATION_URL
    snapshot = await KarlancerReplyReader()._snapshot(
        page,
        previous=(CONVERSATION_URL.replace("7062838", "999"), stale),
    )
    assert snapshot == (CONVERSATION_URL, html)


@pytest.mark.asyncio
async def test_reader_recounts_rooms_loaded_during_discovery_but_keeps_limit():
    html = FIXTURE.read_text(encoding="utf-8")
    unrelated = html.replace("2m6m20w5w850", "other-project")

    class GrowingPage(FakeMessagesPage):
        async def content(self):
            if len(self.rooms) == 1:
                self.rooms.append(html)
            return await super().content()

    target = ReplyWatchTarget(
        project_id="11111111-1111-1111-1111-111111111111",
        title="test",
        project_url=PROJECT_URL,
        project_summary="test",
        submitted_at=OBSERVED_AT,
    )
    page = GrowingPage([unrelated])
    scan = await KarlancerReplyReader(clock=lambda: OBSERVED_AT, max_rooms=2).observe(page, target)
    assert scan.first_reply is not None
    limited = GrowingPage([unrelated])
    with pytest.raises(RuntimeError, match="room discovery limit"):
        await KarlancerReplyReader(clock=lambda: OBSERVED_AT, max_rooms=1).observe(limited, target)
    assert len([event for event in limited.events if event[0] == "click-room"]) == 1


@pytest.mark.asyncio
async def test_unmatched_target_beyond_room_limit_is_not_silently_ignored():
    unrelated = FIXTURE.read_text(encoding="utf-8").replace("2m6m20w5w850", "different-project")
    page = FakeMessagesPage([unrelated, FIXTURE.read_text(encoding="utf-8")])
    target = ReplyWatchTarget(
        project_id="11111111-1111-1111-1111-111111111111",
        title="Synthetic",
        project_url=PROJECT_URL,
        project_summary="Synthetic",
        submitted_at=OBSERVED_AT,
    )
    with pytest.raises(RuntimeError, match="room discovery limit"):
        await KarlancerReplyReader(clock=lambda: OBSERVED_AT, max_rooms=1).observe(page, target)
