"""Deterministic parser for Telegram's public channel preview HTML."""

from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from pydantic import AnyHttpUrl, TypeAdapter

from karlancer_agent.domain.models import ChannelProject

TITLE_LABEL = "عنوان پروژه:"
BUDGET_LABEL = "قیمت پیشنهادی کارفرما:"
DURATION_LABEL = "مدت زمان مورد نظر برای انجام:"
DECORATIVE_PREFIXES = frozenset({"✒", "💰", "⏳", "📅", "🎯", "⚡", "🆔"})
URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class ChannelParseError(ValueError):
    """Raised when Telegram's project message structure is no longer reliable."""


def parse_channel_page(html: str) -> list[ChannelProject]:
    """Parse all complete project messages in ascending message order."""

    soup = BeautifulSoup(html, "lxml")
    containers = soup.select("[data-post]")
    if not containers:
        raise ChannelParseError("Telegram message containers were not found")

    projects: list[ChannelProject] = []
    seen_message_ids: set[int] = set()
    for container in containers:
        message_id = _message_id(container)
        text_container = container.select_one(".tgme_widget_message_text")
        if text_container is None:
            continue
        lines = _text_lines(text_container)
        if TITLE_LABEL not in lines:
            continue

        try:
            title = _between(lines, TITLE_LABEL, BUDGET_LABEL)
            budget = _between(lines, BUDGET_LABEL, DURATION_LABEL)
            duration = _duration(lines)
            project_url = _project_url(container)
        except ChannelParseError as error:
            raise ChannelParseError(f"Malformed project message {message_id}: {error}") from error

        if message_id in seen_message_ids:
            raise ChannelParseError(f"Duplicate Telegram message ID {message_id}")
        seen_message_ids.add(message_id)
        projects.append(
            ChannelProject(
                channel_message_id=message_id,
                title=title,
                budget_text=budget,
                duration_text=duration,
                hashtags=_hashtags(text_container),
                project_url=URL_ADAPTER.validate_python(project_url),
            )
        )

    return sorted(projects, key=lambda project: project.channel_message_id)


def canonicalize_project_url(raw_url: str) -> str:
    """Remove tracking/fragment data and accept only Karlancer project URLs."""

    parsed = urlsplit(raw_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"karlancer.com", "www.karlancer.com"}:
        raise ChannelParseError("project link is not on karlancer.com")
    if not parsed.path.startswith("/projects/"):
        raise ChannelParseError("link is not a Karlancer project URL")

    port = f":{parsed.port}" if parsed.port is not None else ""
    host = f"www.karlancer.com{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", host, path, "", ""))


def _message_id(container: Tag) -> int:
    data_post = container.get("data-post")
    if not isinstance(data_post, str) or "/" not in data_post:
        raise ChannelParseError("Telegram data-post is missing")
    raw_id = data_post.rsplit("/", maxsplit=1)[-1]
    try:
        return int(raw_id)
    except ValueError as error:
        raise ChannelParseError(f"invalid Telegram message ID {raw_id!r}") from error


def _text_lines(text_container: Tag) -> list[str]:
    normalized: list[str] = []
    for raw_line in text_container.get_text("\n", strip=True).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in DECORATIVE_PREFIXES:
            continue
        for prefix in DECORATIVE_PREFIXES:
            if line.startswith(prefix):
                line = line.removeprefix(prefix).strip()
                break
        if line:
            normalized.append(line)
    return normalized


def _between(lines: list[str], start_label: str, end_label: str) -> str:
    try:
        start = lines.index(start_label) + 1
        end = lines.index(end_label, start)
    except ValueError as error:
        raise ChannelParseError(f"required field {start_label!r} is incomplete") from error
    value = " ".join(lines[start:end]).strip()
    if not value:
        raise ChannelParseError(f"required field {start_label!r} is empty")
    return value


def _duration(lines: list[str]) -> str:
    try:
        start = lines.index(DURATION_LABEL) + 1
    except ValueError as error:
        raise ChannelParseError("duration label is missing") from error

    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index].startswith("تاریخ انتشار")
            or lines[index].startswith("برای مشاهده صفحه پروژه")
        ),
        len(lines),
    )
    value = " ".join(lines[start:end]).strip()
    if not value:
        raise ChannelParseError("duration value is missing")
    return value


def _project_url(container: Tag) -> str:
    links = [link for link in container.select("a[href]") if isinstance(link, Tag)]
    preferred = [link for link in links if "مشاهده پروژه" in link.get_text(" ", strip=True)]
    for link in [*preferred, *links]:
        href = link.get("href")
        if not isinstance(href, str) or "/projects/" not in href:
            continue
        try:
            return canonicalize_project_url(href)
        except ChannelParseError:
            continue
    raise ChannelParseError("Karlancer project button/link is missing")


def _hashtags(text_container: Tag) -> tuple[str, ...]:
    ordered: dict[str, None] = {}
    for link in text_container.select("a[href]"):
        hashtag = link.get_text(" ", strip=True)
        if hashtag.startswith("#") and len(hashtag) > 1:
            ordered.setdefault(hashtag, None)
    return tuple(ordered)
