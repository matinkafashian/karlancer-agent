# ruff: noqa: RUF001

from decimal import Decimal

import pytest

from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.llm.schemas import ProposalCore
from karlancer_agent.proposal.templates import FIXED_INTRO, FIXED_OUTRO, PRICE_SENTENCE
from karlancer_agent.proposal.validator import ProposalValidator


def project() -> ExtractedProject:
    return ExtractedProject(
        project_id="322263",
        canonical_url="https://www.karlancer.com/projects/322263/example",
        title="ساخت ربات تلگرام با پایتون",
        description="ربات باید پیام‌ها را با وب‌هوک دریافت و در پایگاه داده ذخیره کند.",
        skills=("Python", "Telegram Bot", "PostgreSQL"),
        budget_text="۱ تا ۳ میلیون تومان",
        budget_min=Decimal("1000000"),
        budget_max=Decimal("3000000"),
        currency="تومان",
        duration_text="۳ روز",
        duration_value=Decimal("3"),
        duration_unit="روز",
        is_open=True,
        proposal_form_url="https://www.karlancer.com/projects/322263/example#proposal",
    )


def valid_core() -> ProposalCore:
    return ProposalCore(
        project_understanding=(
            "برای این ربات تلگرام، پیام‌ها با وب‌هوک دریافت و اطلاعات کاربران در PostgreSQL "
            "ذخیره می‌شود تا پس از راه‌اندازی دوباره داده‌ای از بین نرود."
        ),
        technical_approach=(
            "کار را با پایتون و ساختار ماژولار انجام می‌دهم؛ پردازش پیام و پایگاه داده جدا "
            "خواهند بود. مسیرهای تست و راهنمای استقرار همراه کد تحویل می‌شود."
        ),
        questions=(),
    )


def valid_body() -> str:
    core = valid_core()
    return (
        f"{FIXED_INTRO}\n\n{core.project_understanding}\n\n"
        f"{core.technical_approach}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"
    )


def issue_codes(body: str) -> set[str]:
    result = ProposalValidator().validate(body=body, project=project())
    return {issue.code for issue in result.issues}


def test_approved_proposal_is_valid_and_middle_has_target_length() -> None:
    result = ProposalValidator().validate(body=valid_body(), project=project())

    assert result.is_valid is True
    assert result.issues == ()
    assert 55 <= result.middle_word_count <= 80


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        (lambda body: body.replace(FIXED_INTRO, "سلام"), "fixed_intro"),
        (lambda body: body.replace(FIXED_OUTRO, "در خدمتم."), "fixed_outro"),
        (
            lambda body: body.replace(
                "https://github.com/matinkafashian",
                "https://github.com/changed",
            ),
            "fixed_outro",
        ),
        (lambda body: body.replace(PRICE_SENTENCE, ""), "price_sentence"),
    ),
)
def test_fixed_sections_and_price_sentence_cannot_change(mutation, expected_code: str) -> None:
    assert expected_code in issue_codes(mutation(valid_body()))


@pytest.mark.parametrize(
    "unsafe_text",
    (
        "شماره من 0912 123 4567 است.",
        "شماره من ۰۹۱۲ ۱۲۳ ۴۵۶۷ است.",
        "تلفن دفتر 021 8877 6655 است.",
        "آیدی من @sample_user است.",
        "در t . me / sample_user پیام بدهید.",
        "از طریق telegram.me/sample_user در ارتباط باشیم.",
        "در واتساپ پیام بدهید تا ادامه دهیم.",
        "برای هماهنگی خارج از سایت صحبت کنیم.",
        "جزئیات را در https://example.com ببینید.",
    ),
)
def test_contact_or_off_platform_text_is_rejected(unsafe_text: str) -> None:
    body = valid_body().replace(PRICE_SENTENCE, f"{unsafe_text}\n\n{PRICE_SENTENCE}")

    assert "off_platform_contact" in issue_codes(body)


def test_approved_portfolio_links_do_not_trigger_contact_rule() -> None:
    assert "off_platform_contact" not in issue_codes(valid_body())


def test_more_than_two_questions_is_rejected() -> None:
    body = valid_body().replace(
        PRICE_SENTENCE,
        "نیاز اول چیست؟ نیاز دوم چیست؟ نیاز سوم چیست؟\n\n" + PRICE_SENTENCE,
    )

    assert "too_many_questions" in issue_codes(body)


@pytest.mark.parametrize(
    "cliche",
    (
        "پروژه کاملاً شدنی است",
        "پروژه کاملا شدنی است",
        "پروژه‌تان را کاملاً بررسی کردم",
        "پروژه تون رو کاملا بررسی کردم",
        "صددرصد تضمینی",
        "صد در صد تضمینی",
        "بهترین کیفیت",
    ),
)
def test_banned_cliches_are_rejected(cliche: str) -> None:
    body = valid_body().replace(PRICE_SENTENCE, f"{cliche}.\n\n{PRICE_SENTENCE}")

    assert "banned_cliche" in issue_codes(body)


def test_repeated_middle_paragraph_is_rejected() -> None:
    repeated = valid_core().technical_approach
    body = valid_body().replace(repeated, f"{repeated}\n\n{repeated}")

    assert "repeated_content" in issue_codes(body)


def test_one_long_generic_paragraph_cannot_replace_understanding_and_approach() -> None:
    one_paragraph = (
        "برای ربات تلگرام با پایتون، همه مراحل تحلیل نیاز، طراحی، توسعه، بررسی، آزمون، "
        "تحویل، اجرا، نگهداری و مستندسازی در یک مسیر منظم انجام می‌شود تا نتیجه نهایی "
        "قابل استفاده باشد و تمام بخش‌ها به شکل دقیق کنترل شوند. همچنین روند کار مرحله "
        "به مرحله بررسی می‌شود و تنظیمات لازم، راهنمای اجرا، توضیحات فنی و فایل‌های مورد "
        "نیاز همراه نتیجه نهایی در اختیار کارفرما قرار می‌گیرد تا ادامه کار ساده باشد."
    )
    body = (
        f"{FIXED_INTRO}\n\n{one_paragraph}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"
    )

    assert "middle_structure" in issue_codes(body)


def test_middle_without_project_specific_term_is_rejected() -> None:
    unrelated = (
        "این کار را با یک مسیر منظم و مرحله‌بندی‌شده پیش می‌برم تا هر بخش پس از بررسی "
        "و آزمون تحویل شود. مستندات لازم نیز همراه نتیجه ارائه خواهد شد و در طول اجرا "
        "کیفیت خروجی بررسی می‌شود. "
    ) * 3
    body = f"{FIXED_INTRO}\n\n{unrelated}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"

    assert "project_relevance" in issue_codes(body)


def test_detail_from_description_is_enough_to_make_middle_project_specific() -> None:
    description_project = project().model_copy(
        update={
            "title": "پیاده‌سازی یک سامانه",
            "description": "سامانه باید پلاک خودرو را از تصویر دوربین تشخیص دهد.",
            "skills": (),
        }
    )
    middle = (
        "نیاز اصلی، تشخیص پلاک خودرو از تصویر دوربین و ثبت نتیجه هر پردازش است. "
        "ابتدا ورودی تصویر و کیفیت نمونه‌ها بررسی می‌شود تا محدودیت‌های واقعی داده مشخص "
        "باشد. سپس بخش استخراج ناحیه پلاک، خواندن متن و ذخیره نتیجه به‌صورت ماژول‌های جدا "
        "توسعه پیدا می‌کنند. برای شرایط نور متفاوت و زاویه‌های رایج چند آزمون مشخص در نظر "
        "می‌گیرم و خروجی خطاها نیز ثبت می‌شود. در پایان کد، تنظیمات اجرا، تست‌های اصلی و "
        "راهنمای استقرار تحویل داده می‌شود تا نگهداری و توسعه بعدی مسیر روشنی داشته باشد."
    )
    body = f"{FIXED_INTRO}\n\n{middle}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"

    result = ProposalValidator().validate(body=body, project=description_project)

    assert "project_relevance" not in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    ("middle", "expected_code"),
    (
        ("ربات تلگرام پایتون", "middle_too_short"),
        (("ربات تلگرام پایتون و پایگاه داده " * 30).strip(), "middle_too_long"),
    ),
)
def test_middle_length_bounds_are_enforced(middle: str, expected_code: str) -> None:
    body = f"{FIXED_INTRO}\n\n{middle}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"

    assert expected_code in issue_codes(body)
