"""Read-only discovery, activity signaling, and parsing of employer replies."""

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
from uuid import UUID

from bs4 import BeautifulSoup, Tag
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict

from karlancer_agent.domain.models import FirstReply

_TEHRAN = timezone(timedelta(hours=3, minutes=30))
_ACTIVE_ROOM = "#messageBox app-room-messages:not(.d-none)"

_PERSIAN_MONTHS = {
    "فروردین": 1,
    "اردیبهشت": 2,
    "خرداد": 3,
    "تیر": 4,
    "مرداد": 5,
    "شهریور": 6,
    "مهر": 7,
    "آبان": 8,
    "آذر": 9,
    "دی": 10,
    "بهمن": 11,
    "اسفند": 12,
}
_PERSIAN_WEEKDAYS = {
    "دوشنبه": 0,
    "دو شنبه": 0,
    "سه شنبه": 1,
    "سه‌شنبه": 1,
    "چهارشنبه": 2,
    "چهار شنبه": 2,
    "پنجشنبه": 3,
    "پنج شنبه": 3,
    "جمعه": 4,
    "شنبه": 5,
    "یکشنبه": 6,
    "یک شنبه": 6,
}


class ConversationScan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    matched_project: bool
    conversation_url: AnyHttpUrl
    first_reply: FirstReply | None = None


class ReplyWatchTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: UUID
    title: str
    project_url: AnyHttpUrl
    project_summary: str
    submitted_at: AwareDatetime
    conversation_url: AnyHttpUrl | None = None
    requires_reconciliation: bool = False


class RoomLocator(Protocol):
    async def count(self) -> int: ...

    def nth(self, index: int) -> "RoomLocator": ...

    async def click(self) -> None: ...


class ReplyPage(Protocol):
    @property
    def url(self) -> str: ...

    async def goto(self, url: str, **kwargs: object) -> object: ...

    def locator(self, selector: str) -> RoomLocator: ...

    async def wait_for_selector(self, selector: str, **kwargs: object) -> object: ...

    async def wait_for_timeout(self, timeout: float) -> None: ...

    async def content(self) -> str: ...


class ActivityPage(ReplyPage, Protocol):
    def on(self, event: str, handler: Callable[[Any], None]) -> None: ...


class ReplyActivitySignal:
    """Wake scans on Karlancer message traffic, with a bounded timeout fallback."""

    def __init__(self, page: ActivityPage) -> None:
        self._event = asyncio.Event()
        page.on("response", self._on_response)
        page.on("websocket", self._on_websocket)

    def clear(self) -> None:
        self._event.clear()

    async def wait(self, timeout_seconds: float = 30) -> Literal["event", "timeout"]:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return "timeout"
        return "event"

    def _on_response(self, response: Any) -> None:
        if _is_message_activity(str(getattr(response, "url", ""))):
            self._event.set()

    def _on_websocket(self, websocket: Any) -> None:
        if not _is_karlancer_url(str(getattr(websocket, "url", ""))):
            return
        websocket.on("framereceived", lambda _frame: self._event.set())


def _is_message_activity(url: str) -> bool:
    lowered = unquote(url).lower()
    return _is_karlancer_url(lowered) and any(
        marker in lowered for marker in ("/messages", "/message", "room=")
    )


def _is_karlancer_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "karlancer.com" or host.endswith(".karlancer.com")


class KarlancerReplyReader:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_rooms: int = 30,
    ) -> None:
        if max_rooms < 1:
            raise ValueError("max_rooms must be positive")
        self._clock = clock
        self._max_rooms = max_rooms
        self._room_snapshots: list[tuple[str, str]] = []
        self._rooms_loaded = False
        self._discovery_truncated = False

    def begin_scan(self) -> None:
        """Share one room discovery across all targets in this polling cycle."""
        self._room_snapshots.clear()
        self._rooms_loaded = False
        self._discovery_truncated = False

    async def observe(self, page: ReplyPage, target: ReplyWatchTarget) -> ConversationScan:
        for cached_url, html in self._room_snapshots:
            scan = parse_conversation(
                html,
                project_id=str(target.project_id),
                project_url=str(target.project_url),
                conversation_url=cached_url,
                observed_at=self._clock(),
            )
            if scan.matched_project:
                return scan
        messages_url = "https://www.karlancer.com/panel/messages"
        if target.conversation_url is not None and _room_id(str(target.conversation_url)):
            direct_url = str(target.conversation_url)
            await page.goto(direct_url, wait_until="commit")
            try:
                snapshot = await self._snapshot(page, expected_room=_room_id(direct_url))
            except PlaywrightTimeoutError:
                # A stored room can disappear or redirect after Karlancer changes the
                # conversation list. Fall back to a fresh read-only room discovery.
                snapshot = None
            if snapshot is not None:
                room_url, html = snapshot
                scan = parse_conversation(
                    html,
                    project_id=str(target.project_id),
                    project_url=str(target.project_url),
                    conversation_url=room_url,
                    observed_at=self._clock(),
                )
                if scan.matched_project:
                    self._room_snapshots.append(snapshot)
                    return scan
        if self._rooms_loaded:
            if self._discovery_truncated:
                raise RuntimeError("room discovery limit reached; unmatched target needs review")
            return ConversationScan.model_validate(
                {"matched_project": False, "conversation_url": messages_url}
            )
        await page.goto(messages_url, wait_until="commit")
        await page.wait_for_selector(
            ".room-item",
            state="attached",
            timeout=15_000,
        )
        matched = None
        previous_snapshot = None
        for index in range(self._max_rooms):
            # Recount after each click: Angular may append already-loading rooms.
            if index >= await page.locator(".room-item").count():
                break
            await page.locator(".room-item").nth(index).click()
            snapshot = await self._snapshot(page, previous=previous_snapshot)
            if snapshot is None:
                continue
            conversation_url, html = snapshot
            previous_snapshot = snapshot
            self._room_snapshots.append(snapshot)
            scan = parse_conversation(
                html,
                project_id=str(target.project_id),
                project_url=str(target.project_url),
                conversation_url=conversation_url,
                observed_at=self._clock(),
            )
            if scan.matched_project:
                matched = scan
        self._rooms_loaded = True
        self._discovery_truncated = await page.locator(".room-item").count() > self._max_rooms
        if matched is not None:
            return matched
        if self._discovery_truncated:
            raise RuntimeError("room discovery limit reached; unmatched target needs review")
        return ConversationScan.model_validate(
            {"matched_project": False, "conversation_url": messages_url}
        )

    async def _snapshot(
        self,
        page: ReplyPage,
        *,
        expected_room: str | None = None,
        previous: tuple[str, str] | None = None,
    ) -> tuple[str, str] | None:
        try:
            await page.wait_for_selector(_ACTIVE_ROOM, state="visible", timeout=10_000)
        except PlaywrightTimeoutError:
            # An individual room may be stale or still loading. It is not an
            # account-wide outage; skip it and continue the bounded discovery.
            return None
        last = None
        for _ in range(8):
            await page.wait_for_timeout(250)
            url = page.url
            html = await page.content()
            room_id = _room_id(url)
            room = _active_room(BeautifulSoup(html, "lxml"))
            if room_id is None or room is None or (expected_room and room_id != expected_room):
                last = None
                continue
            signature = (room_id, str(room))
            if previous is not None:
                previous_room = _active_room(BeautifulSoup(previous[1], "lxml"))
                if room_id != _room_id(previous[0]) and str(room) == str(previous_room):
                    last = None
                    continue
            if signature == last:
                return url, html
            last = signature
        return None


def _room_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if not _is_karlancer_url(url) or parsed.path.rstrip("/") != "/panel/messages":
        return None
    values = parse_qs(parsed.query).get("room", [])
    return values[0] if len(values) == 1 and values[0].isdigit() else None


def _hidden(tag: Tag) -> bool:
    return any(
        isinstance(node, Tag)
        and (
            node.has_attr("hidden")
            or node.get("aria-hidden") == "true"
            or "d-none" in str(node.get("class", ""))
            or re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", str(node.get("style", "")), re.I
            )
        )
        for node in [tag, *tag.parents]
    )


def _active_room(soup: BeautifulSoup) -> Tag | None:
    rooms = [room for room in soup.select("#messageBox app-room-messages") if not _hidden(room)]
    return rooms[0] if len(rooms) == 1 else None


def parse_conversation(
    html: str,
    *,
    project_id: str,
    project_url: str,
    conversation_url: str,
    observed_at: datetime,
) -> ConversationScan:
    """Return the first incoming message after this project's bid card."""

    soup = BeautifulSoup(html, "lxml")
    room = _active_room(soup)
    if room is None:
        return ConversationScan.model_validate(
            {"matched_project": False, "conversation_url": conversation_url}
        )
    expected_url = _normalized_project_url(project_url)
    expected_identity = _project_identity(project_url)
    matching_links = [
        link
        for link in room.select("a[href*='/projects/']")
        if not _hidden(link)
        if (
            expected_identity is not None
            and _project_identity(str(link.get("href", ""))) == expected_identity
        )
        or _normalized_project_url(str(link.get("href", ""))) == expected_url
    ]
    if not matching_links:
        return ConversationScan.model_validate(
            {"matched_project": False, "conversation_url": conversation_url}
        )

    bid_anchor = matching_links[-1]
    bid_container = _numeric_container(bid_anchor)
    if bid_container is None:
        return ConversationScan.model_validate(
            {"matched_project": False, "conversation_url": conversation_url}
        )

    for candidate in bid_container.find_all_next():
        if room not in candidate.parents:
            break
        if _hidden(candidate):
            continue
        if (
            candidate.name == "a"
            and "/projects/" in str(candidate.get("href", ""))
            and bid_container not in candidate.parents
            and _numeric_container(candidate) is not None
        ):
            break
        if not re.fullmatch(r"message_\d+", str(candidate.get("id", ""))):
            continue
        raw_classes = candidate.get("class")
        classes = set(raw_classes) if isinstance(raw_classes, list) else set()
        if "flex-row-reverse" not in classes:
            continue
        message_id = str(candidate.get("id", "")).removeprefix("message_")
        body = candidate.find("div", id=message_id, recursive=False)
        if body is None:
            continue
        text = _message_text(body)
        if not text:
            text = "[پاسخ کارفرما فقط شامل فایل یا پیوست است]"
        received_at = _message_time(
            body,
            message_container=candidate,
            observed_at=observed_at,
        )
        reply = FirstReply.model_validate(
            {
                "project_id": project_id,
                "employer_message_id": message_id,
                "text": text,
                "received_at": received_at,
                "conversation_url": conversation_url,
            }
        )
        return ConversationScan.model_validate(
            {
                "matched_project": True,
                "conversation_url": conversation_url,
                "first_reply": reply,
            }
        )
    return ConversationScan.model_validate(
        {"matched_project": True, "conversation_url": conversation_url}
    )


def _numeric_container(link: Tag) -> Tag | None:
    for parent in link.parents:
        if isinstance(parent, Tag) and re.fullmatch(r"\d+", str(parent.get("id", ""))):
            return parent
    return None


def _normalized_project_url(value: str) -> str:
    absolute = urljoin("https://www.karlancer.com", value)
    parsed = urlsplit(unquote(absolute))
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _project_identity(value: str) -> str | None:
    path = urlsplit(unquote(urljoin("https://www.karlancer.com", value))).path
    tail = path.rstrip("/").rsplit("/", 1)[-1].rsplit("-", 1)[-1].lower()
    return tail if re.fullmatch(r"[a-z0-9]{8,32}", tail) else None


def _message_text(body: Tag) -> str:
    copy = BeautifulSoup(str(body), "lxml")
    for timestamp in copy.select("span.text-left.fs-10"):
        timestamp.decompose()
    return " ".join(copy.get_text(" ", strip=True).split())


def _message_time(body: Tag, *, message_container: Tag, observed_at: datetime) -> datetime:
    observed_at = observed_at.astimezone(_TEHRAN)
    timestamp = body.select_one("span.text-left.fs-10")
    if timestamp is None:
        return observed_at.astimezone(UTC)
    normalized = timestamp.get_text(" ", strip=True).translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    )
    match = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})", normalized)
    if match is None:
        return observed_at.astimezone(UTC)
    message_date = _preceding_persian_date(
        message_container,
        observed_at=observed_at,
    )
    result = observed_at.replace(
        year=message_date.year,
        month=message_date.month,
        day=message_date.day,
        hour=int(match.group("hour")),
        minute=int(match.group("minute")),
        second=0,
        microsecond=0,
    )
    if result > observed_at + timedelta(minutes=5):
        result -= timedelta(days=1)
    return result.astimezone(UTC)


def _preceding_persian_date(message: Tag, *, observed_at: datetime) -> date:
    month_pattern = "|".join(_PERSIAN_MONTHS)
    pattern = re.compile(rf"(?P<day>\d{{1,2}})\s+(?P<month>{month_pattern})")
    translation = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹يك",
        "0123456789یک",
    )
    room = message.find_parent("app-room-messages")
    if room is None:
        return observed_at.date()
    for raw_text in message.find_all_previous(string=True):
        if room not in raw_text.parents:
            break
        normalized = " ".join(str(raw_text).translate(translation).replace("\u200c", " ").split())
        if normalized == "امروز":
            return observed_at.date()
        if normalized == "دیروز":
            return observed_at.date() - timedelta(days=1)
        weekday = _PERSIAN_WEEKDAYS.get(normalized)
        if weekday is not None:
            days_ago = (observed_at.weekday() - weekday) % 7
            return observed_at.date() - timedelta(days=days_ago)
        match = pattern.search(normalized)
        if match is None:
            continue
        jalali_day = int(match.group("day"))
        jalali_month = _PERSIAN_MONTHS[match.group("month")]
        candidates = (
            _jalali_to_gregorian(observed_at.year - 621, jalali_month, jalali_day),
            _jalali_to_gregorian(observed_at.year - 622, jalali_month, jalali_day),
        )
        plausible = [
            candidate
            for candidate in candidates
            if candidate <= observed_at.date() + timedelta(days=1)
        ]
        if plausible:
            return max(plausible)
    return observed_at.date()


def _jalali_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert a Jalali calendar date using the 33-year arithmetic cycle."""

    jalali_year = year + 1595
    days = (
        -355668 + 365 * jalali_year + (jalali_year // 33) * 8 + ((jalali_year % 33 + 3) // 4) + day
    )
    days += (month - 1) * 31 if month < 7 else (month - 7) * 30 + 186
    gregorian_year = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gregorian_year += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gregorian_year += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gregorian_year += (days - 1) // 365
        days = (days - 1) % 365
    month_lengths = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if gregorian_year % 4 == 0 and (gregorian_year % 100 != 0 or gregorian_year % 400 == 0):
        month_lengths[2] = 29
    gregorian_day = days + 1
    gregorian_month = 1
    while gregorian_month <= 12 and gregorian_day > month_lengths[gregorian_month]:
        gregorian_day -= month_lengths[gregorian_month]
        gregorian_month += 1
    return date(gregorian_year, gregorian_month, gregorian_day)
