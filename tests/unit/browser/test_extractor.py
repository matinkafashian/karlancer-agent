from decimal import Decimal
from pathlib import Path

import pytest

from karlancer_agent.browser.extractor import (
    ExtractionError,
    ProjectExtractor,
    parse_budget,
    parse_duration,
    parse_project_page,
)
from karlancer_agent.browser.selectors import PROJECT_ROOT

PROGRAMMING_FIXTURE = Path("tests/fixtures/project_ai_programming.html")
TOOL_ONLY_FIXTURE = Path("tests/fixtures/project_tool_only.html")


def test_parse_persian_budget_uses_advertised_maximum() -> None:
    budget = parse_budget("بودجه از ۸۰۰,۰۰۰ تومان تا ۳,۰۰۰,۰۰۰ تومان")  # noqa: RUF001

    assert budget.minimum == Decimal("800000")
    assert budget.maximum == Decimal("3000000")
    assert budget.currency == "TOMAN"


def test_parse_single_english_budget_uses_same_minimum_and_maximum() -> None:
    budget = parse_budget("Budget 5000000 IRR")

    assert budget.minimum == Decimal("5000000")
    assert budget.maximum == Decimal("5000000")
    assert budget.currency == "IRR"


@pytest.mark.parametrize(
    ("raw", "value", "unit"),
    [
        ("زمان پیشنهادی ۱ روز", Decimal("1"), "day"),  # noqa: RUF001
        ("3 days", Decimal("3"), "day"),
        ("۲ هفته", Decimal("2"), "week"),
    ],
)
def test_parse_duration_normalizes_value_and_unit(
    raw: str, value: Decimal, unit: str
) -> None:
    duration = parse_duration(raw)

    assert duration.value == value
    assert duration.unit == unit


def test_parse_project_page_extracts_all_submission_inputs() -> None:
    project = parse_project_page(
        PROGRAMMING_FIXTURE.read_text(encoding="utf-8"),
        "https://www.karlancer.com/projects/food-scraper-2w6o5o0r46ne?source=telegram",
    )

    assert project.project_id == "322263"
    assert str(project.canonical_url).endswith("/food-scraper-2w6o5o0r46ne")
    assert project.title == "استخراج محصول غذایی از فروشگاه‌های آنلاین"
    assert project.description.startswith("نمونه کار استخراج")
    assert project.skills == ("python", "پایتون", "ساخت ربات")
    assert project.budget_min == Decimal("800000")
    assert project.budget_max == Decimal("3000000")
    assert project.budget_text == "از ۸۰۰,۰۰۰ تومان تا ۳,۰۰۰,۰۰۰ تومان"  # noqa: RUF001
    assert project.currency == "TOMAN"
    assert project.duration_value == Decimal("1")
    assert project.duration_unit == "day"
    assert project.duration_text == "۱ روز"  # noqa: RUF001
    assert project.is_open is True
    assert project.already_submitted is False
    assert project.employer_display_id == "کارفرمای جدید"
    assert project.proposal_form_url == project.canonical_url


def test_tool_only_description_is_preserved_for_later_rejection() -> None:
    project = parse_project_page(
        TOOL_ONLY_FIXTURE.read_text(encoding="utf-8"),
        "https://www.karlancer.com/projects/ai-video-editing-2m6m2re09850",
    )

    assert project.title == "ادیت ۴ کلیپ کودکانه با هوش مصنوعی"
    assert "ابزارهای آماده" in project.description
    assert project.skills == ("ادیت ویدئو", "تولید محتوا")


def test_closed_project_uses_slug_identity_and_is_not_open() -> None:
    html = PROGRAMMING_FIXTURE.read_text(encoding="utf-8")
    html = html.replace("منتشر شده", "بسته شده").replace(
        '<div id="setBidBtn322263">ثبت پیشنهاد روی پروژه</div>', ""
    )

    project = parse_project_page(
        html, "https://www.karlancer.com/projects/food-scraper-2w6o5o0r46ne"
    )

    assert project.project_id == "slug:2w6o5o0r46ne"
    assert project.is_open is False


def test_existing_proposal_marker_is_detected() -> None:
    html = PROGRAMMING_FIXTURE.read_text(encoding="utf-8").replace(
        "ثبت پیشنهاد روی پروژه", "پیشنهاد شما ثبت شده"
    )

    project = parse_project_page(
        html, "https://www.karlancer.com/projects/food-scraper-2w6o5o0r46ne"
    )

    assert project.already_submitted is True


@pytest.mark.asyncio
async def test_playwright_adapter_navigates_read_only_and_parses_content() -> None:
    class FakePage:
        url = "https://www.karlancer.com/projects/food-scraper-2w6o5o0r46ne"

        def __init__(self) -> None:
            self.visited: tuple[str, str] | None = None
            self.waited_for: tuple[str, str, int] | None = None

        async def goto(self, url: str, **kwargs: object) -> None:
            self.visited = (url, str(kwargs["wait_until"]))

        async def wait_for_selector(self, selector: str, **kwargs: object) -> None:
            self.waited_for = (
                selector,
                str(kwargs["state"]),
                int(kwargs["timeout"]),
            )

        async def content(self) -> str:
            return PROGRAMMING_FIXTURE.read_text(encoding="utf-8")

    page = FakePage()
    project = await ProjectExtractor().extract(page, page.url)

    assert page.visited == (page.url, "domcontentloaded")
    assert page.waited_for == (PROJECT_ROOT, "visible", 30_000)
    assert project.project_id == "322263"


def test_missing_budget_stops_extraction() -> None:
    html = """
    <app-project-card type="project-bids">
      <h1>Broken project</h1>
      <div>زمان پیشنهادی <strong>3 days</strong></div>
      <div>وضعیت <strong>منتشر شده</strong></div>
      <div id="setBidBtn999">ثبت پیشنهاد روی پروژه</div>
      <div class="white-space-pre-line">Description</div>
    </app-project-card>
    """

    with pytest.raises(ExtractionError, match="budget"):
        parse_project_page(html, "https://www.karlancer.com/projects/broken-999")
