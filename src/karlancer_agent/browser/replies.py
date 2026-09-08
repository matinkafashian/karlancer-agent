"""Read-only discovery and parsing of Karlancer employer replies."""

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import UUID

from bs4 import BeautifulSoup, Tag
from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict

from karlancer_agent.domain.models import FirstReply

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

    async def content(self) -> str: ...


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

    async def observe(
        self, page: ReplyPage, target: ReplyWatchTarget
    ) -> ConversationScan:
        if target.conversation_url is not None:
            url = str(target.conversation_url)
            await page.goto(url, wait_until="domcontentloaded")
            return parse_conversation(
                await page.content(),
                project_id=str(target.project_id),
                project_url=str(target.project_url),
                conversation_url=url,
                observed_at=self._clock(),
            )

        messages_url = "https://www.karlancer.com/panel/messages"
        await page.goto(messages_url, wait_until="domcontentloaded")
        room_count = min(await page.locator(".room-item").count(), self._max_rooms)
        for index in range(room_count):
            await page.locator(".room-item").nth(index).click()
            await page.wait_for_selector(
                "#messageBox app-room-messages:not(.d-none)",
                state="visible",
                timeout=10_000,
            )
            conversation_url = page.url
            scan = parse_conversation(
                await page.content(),
                project_id=str(target.project_id),
                project_url=str(target.project_url),
                conversation_url=conversation_url,
                observed_at=self._clock(),
            )
            if scan.matched_project:
                return scan
        return ConversationScan.model_validate(
            {"matched_project": False, "conversation_url": messages_url}
        )


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
    expected_url = _normalized_project_url(project_url)
    matching_links = [
        link
        for link in soup.select("#messageBox a[href*='/projects/']")
        if _normalized_project_url(str(link.get("href", ""))) == expected_url
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

    for candidate in bid_container.find_all_next(id=re.compile(r"^message_\d+$")):
        if not isinstance(candidate, Tag):
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
    parsed = urlsplit(unquote(value))
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _message_text(body: Tag) -> str:
    copy = BeautifulSoup(str(body), "lxml")
    for timestamp in copy.select("span.text-left.fs-10"):
        timestamp.decompose()
    return " ".join(copy.get_text(" ", strip=True).split())


def _message_time(
    body: Tag, *, message_container: Tag, observed_at: datetime
) -> datetime:
    timestamp = body.select_one("span.text-left.fs-10")
    if timestamp is None:
        return observed_at
    normalized = timestamp.get_text(" ", strip=True).translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    )
    match = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})", normalized)
    if match is None:
        return observed_at
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
    return result


def _preceding_persian_date(message: Tag, *, observed_at: datetime) -> date:
    month_pattern = "|".join(_PERSIAN_MONTHS)
    pattern = re.compile(rf"(?P<day>\d{{1,2}})\s+(?P<month>{month_pattern})")
    translation = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹يك",
        "0123456789یک",
    )
    for raw_text in message.find_all_previous(string=True):
        normalized = " ".join(
            str(raw_text).translate(translation).replace("\u200c", " ").split()
        )
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
        -355668
        + 365 * jalali_year
        + (jalali_year // 33) * 8
        + ((jalali_year % 33 + 3) // 4)
        + day
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
    if gregorian_year % 4 == 0 and (
        gregorian_year % 100 != 0 or gregorian_year % 400 == 0
    ):
        month_lengths[2] = 29
    gregorian_day = days + 1
    gregorian_month = 1
    while gregorian_month <= 12 and gregorian_day > month_lengths[gregorian_month]:
        gregorian_day -= month_lengths[gregorian_month]
        gregorian_month += 1
    return date(gregorian_year, gregorian_month, gregorian_day)
