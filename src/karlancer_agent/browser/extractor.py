"""Extract and normalize data from one Karlancer project page."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from bs4 import BeautifulSoup, Tag
from pydantic import AnyHttpUrl, TypeAdapter

from karlancer_agent.browser.selectors import (
    CANONICAL_LINK,
    PROJECT_DESCRIPTION,
    PROJECT_ROOT,
    PROJECT_SKILLS,
    PROJECT_TITLE,
    SUBMIT_CONTROL,
)
from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.ingestion.parser import (
    ChannelParseError,
    canonicalize_project_url,
)

URL_ADAPTER = TypeAdapter(AnyHttpUrl)
DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
NUMBER_PATTERN = re.compile(r"\d[\d,٬،.]*")
DURATION_UNITS = {
    "روز": "day",
    "day": "day",
    "days": "day",
    "هفته": "week",
    "week": "week",
    "weeks": "week",
    "ماه": "month",
    "month": "month",
    "months": "month",
    "ساعت": "hour",
    "hour": "hour",
    "hours": "hour",
}


class ExtractionError(ValueError):
    """Raised when a required submission field cannot be trusted."""


@dataclass(frozen=True, slots=True)
class BudgetRange:
    minimum: Decimal
    maximum: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Duration:
    value: Decimal
    unit: str
    display_text: str


class PageLike(Protocol):
    """Subset of Playwright Page used by the extractor."""

    @property
    def url(self) -> str: ...

    async def goto(self, url: str, **kwargs: Any) -> object: ...

    async def content(self) -> str: ...


class ProjectExtractor:
    """Read-only Playwright adapter around the deterministic HTML parser."""

    async def extract(self, page: PageLike, url: str) -> ExtractedProject:
        await page.goto(url, wait_until="domcontentloaded")
        return parse_project_page(await page.content(), page.url)


def parse_budget(raw: str) -> BudgetRange:
    """Parse displayed money and retain the advertised maximum."""

    normalized = raw.translate(DIGIT_TRANSLATION)
    values = [_decimal_number(match) for match in NUMBER_PATTERN.findall(normalized)]
    if not values:
        raise ExtractionError("budget does not contain a numeric value")
    if "تومان" in raw:
        currency = "TOMAN"
    elif "ریال" in raw or re.search(r"\bIRR\b", raw, re.IGNORECASE):
        currency = "IRR"
    else:
        raise ExtractionError("budget currency is missing or unsupported")
    return BudgetRange(minimum=min(values), maximum=max(values), currency=currency)


def parse_duration(raw: str) -> Duration:
    """Normalize project duration while preserving its displayed value."""

    display = re.sub(r"^\s*زمان پیشنهادی\s*", "", raw).strip()
    normalized = display.translate(DIGIT_TRANSLATION).lower()
    number_match = NUMBER_PATTERN.search(normalized)
    if number_match is None:
        raise ExtractionError("duration does not contain a numeric value")
    value = _decimal_number(number_match.group())
    unit = next(
        (canonical for token, canonical in DURATION_UNITS.items() if token in normalized),
        None,
    )
    if unit is None:
        raise ExtractionError("duration unit is missing or unsupported")
    return Duration(value=value, unit=unit, display_text=display)


def parse_project_page(html: str, current_url: str) -> ExtractedProject:
    """Extract all fields needed by classification and safe form submission."""

    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one(PROJECT_ROOT)
    if not isinstance(root, Tag):
        raise ExtractionError("Karlancer project card was not found")

    title = _required_text(root.select_one(PROJECT_TITLE.removeprefix(f"{PROJECT_ROOT} ")), "title")
    description = _required_text(
        root.select_one(PROJECT_DESCRIPTION.removeprefix(f"{PROJECT_ROOT} ")),
        "description",
    )
    canonical_url = _canonical_url(soup, current_url)
    budget_text = _labelled_text(root, "بودجه")
    budget = parse_budget(budget_text)
    duration = parse_duration(_labelled_text(root, "زمان پیشنهادی"))
    status_text = _labelled_text(root, "وضعیت")
    submit_control = root.select_one(SUBMIT_CONTROL.removeprefix(f"{PROJECT_ROOT} "))
    project_id = _project_id(submit_control, canonical_url)
    page_text = _clean_text(root.get_text(" ", strip=True))
    already_submitted = any(
        marker in page_text
        for marker in ("پیشنهاد شما", "قبلاً پیشنهاد", "پیشنهاد ثبت شده")
    )
    is_open = "منتشر شده" in status_text and submit_control is not None

    skills = tuple(
        dict.fromkeys(
            _clean_text(link.get_text(" ", strip=True))
            for link in root.select(PROJECT_SKILLS.removeprefix(f"{PROJECT_ROOT} "))
            if _clean_text(link.get_text(" ", strip=True))
        )
    )
    employer = _optional_employer(root)
    canonical = URL_ADAPTER.validate_python(canonical_url)
    return ExtractedProject(
        project_id=project_id,
        canonical_url=canonical,
        title=title,
        description=description,
        skills=skills,
        budget_text=re.sub(r"^بودجه\s*", "", budget_text).strip(),
        budget_min=budget.minimum,
        budget_max=budget.maximum,
        currency=budget.currency,
        duration_text=duration.display_text,
        duration_value=duration.value,
        duration_unit=duration.unit,
        is_open=is_open,
        already_submitted=already_submitted,
        employer_display_id=employer,
        proposal_form_url=canonical,
    )


def _decimal_number(raw: str) -> Decimal:
    cleaned = raw.replace(",", "").replace("٬", "").replace("،", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as error:
        raise ExtractionError(f"invalid numeric value {raw!r}") from error


def _labelled_text(root: Tag, label: str) -> str:
    field_name = {
        "بودجه": "budget",
        "زمان پیشنهادی": "duration",
        "وضعیت": "status",
    }.get(label, label)
    text_node = root.find(string=lambda value: bool(value and label in _clean_text(value)))
    if text_node is None or text_node.parent is None:
        raise ExtractionError(f"required {field_name} field was not found")
    container: Tag = text_node.parent
    if _clean_text(container.get_text(" ", strip=True)) == label and container.parent:
        container = container.parent
    value = _clean_text(container.get_text(" ", strip=True))
    if label not in value:
        raise ExtractionError(f"required {field_name} field was incomplete")
    return value


def _required_text(element: Tag | None, field: str) -> str:
    if element is None:
        raise ExtractionError(f"required {field} field was not found")
    value = _clean_text(element.get_text(" ", strip=True))
    if not value:
        raise ExtractionError(f"required {field} field was empty")
    return value


def _canonical_url(soup: BeautifulSoup, current_url: str) -> str:
    link = soup.select_one(CANONICAL_LINK)
    raw = link.get("href") if isinstance(link, Tag) else current_url
    if not isinstance(raw, str):
        raise ExtractionError("canonical project URL was not found")
    try:
        return canonicalize_project_url(raw)
    except ChannelParseError as error:
        raise ExtractionError("canonical project URL is invalid") from error


def _project_id(submit_control: Tag | None, canonical_url: str) -> str:
    if submit_control is not None:
        raw_id = submit_control.get("id")
        match = re.fullmatch(r"setBidBtn(\d+)", raw_id) if isinstance(raw_id, str) else None
        if match:
            return match.group(1)
    slug_id = canonical_url.rsplit("-", maxsplit=1)[-1]
    if slug_id:
        return f"slug:{slug_id}"
    raise ExtractionError("project ID was not found")


def _optional_employer(root: Tag) -> str | None:
    text_node = root.find(
        string=lambda value: bool(value and "کارفرمای" in _clean_text(value))
    )
    return _clean_text(text_node) if text_node else None


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()
