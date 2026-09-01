from pathlib import Path

import pytest

from karlancer_agent.ingestion.parser import ChannelParseError, parse_channel_page

FIXTURE = Path("tests/fixtures/channel_page.html")


def test_parser_extracts_complete_projects_and_ignores_channel_notice() -> None:
    projects = parse_channel_page(FIXTURE.read_text(encoding="utf-8"))

    assert [project.channel_message_id for project in projects] == [7001, 7003]
    assert projects[0].title == "پیاده‌سازی سامانه RAG"
    assert projects[0].budget_text == "بین ۱۰,۰۰۰,۰۰۰ تا ۲۰,۰۰۰,۰۰۰ تومان"  # noqa: RUF001
    assert projects[0].duration_text == "۷ روز"  # noqa: RUF001
    assert projects[0].hashtags == ("#پایتون", "#RAG", "#LLM")
    assert str(projects[0].project_url) == (
        "https://www.karlancer.com/projects/rag-service-abc123"
    )
    assert projects[1].hashtags == ("#Python", "#telegram_bot")
    assert str(projects[1].project_url).endswith("/telegram-bot-training-xyz789")


def test_parser_rejects_page_without_telegram_messages() -> None:
    with pytest.raises(ChannelParseError, match="message containers"):
        parse_channel_page("<html><body>maintenance</body></html>")


def test_parser_rejects_project_message_missing_duration() -> None:
    html = """
    <div class="tgme_widget_message" data-post="karlancer_projects/9001">
      <div class="tgme_widget_message_text">
        ✒عنوان پروژه:<br>Broken project<br>
        💰قیمت پیشنهادی کارفرما:<br>1000 IRR<br>
        🎯 برای مشاهده صفحه پروژه و ثبت پیشنهاد به لینک زیر مراجعه کنید:
        <a href="https://www.karlancer.com/projects/broken-9001">link</a>
      </div>
    </div>
    """

    with pytest.raises(ChannelParseError, match="9001"):
        parse_channel_page(html)
