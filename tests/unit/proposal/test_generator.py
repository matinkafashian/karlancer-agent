# ruff: noqa: RUF001

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.llm.schemas import ProposalCore
from karlancer_agent.proposal.generator import ProposalGenerator
from karlancer_agent.proposal.templates import FIXED_INTRO, FIXED_OUTRO, PRICE_SENTENCE


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


def valid_core(*, questions: tuple[str, ...] = ()) -> ProposalCore:
    return ProposalCore(
        project_understanding=(
            "برای این ربات تلگرام، پیام‌ها با وب‌هوک دریافت و اطلاعات کاربران در PostgreSQL "
            "ذخیره می‌شود تا پس از راه‌اندازی دوباره داده‌ای از بین نرود."
        ),
        technical_approach=(
            "کار را با پایتون و ساختار ماژولار انجام می‌دهم؛ پردازش پیام و پایگاه داده جدا "
            "خواهند بود. مسیرهای تست و راهنمای استقرار همراه کد تحویل می‌شود."
        ),
        questions=questions,
    )


def test_generator_wraps_project_specific_core_with_approved_fixed_text() -> None:
    generated_at = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)

    draft = ProposalGenerator().build(
        project=project(),
        core=valid_core(),
        provider_name="openai-compatible",
        model_name="test-model",
        prompt_version="proposal-core-v1",
        generated_at=generated_at,
    )

    expected = (
        f"{FIXED_INTRO}\n\n"
        f"{valid_core().project_understanding}\n\n"
        f"{valid_core().technical_approach}\n\n"
        f"{PRICE_SENTENCE}\n\n"
        f"{FIXED_OUTRO}"
    )
    assert draft.body == expected
    assert draft.project_id == "322263"
    assert draft.generated_at == generated_at


def test_generator_preserves_identity_but_never_adds_external_links() -> None:
    draft = ProposalGenerator().build(
        project=project(),
        core=valid_core(),
        provider_name="openai-compatible",
        model_name="test-model",
        prompt_version="proposal-core-v1",
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    approved_intro = (
        "سلام، وقت بخیر.\n"
        "من متین کفاشیان هستم؛ دارای دکترای هوش مصنوعی و علوم کامپیوتر و چند مدرک "
        "تخصصی هوش مصنوعی از آمریکا."
    )
    approved_outro = "اگر سؤال دیگری بود، در خدمتم."
    assert draft.body.startswith(f"{approved_intro}\n\n")
    assert draft.body.endswith(f"\n\n{approved_outro}")
    assert "چند روزی" not in draft.body
    assert "چند پروژه مشابه" not in draft.body
    assert "http" not in draft.body.lower()
    assert "www." not in draft.body.lower()
    assert "youtube" not in draft.body.lower()
    assert "github" not in draft.body.lower()
    assert "linkedin" not in draft.body.lower()
    assert "تقریباً به‌طور کامل متوجه نیاز" not in draft.body
    assert len(draft.body.split()) <= 210


def test_generator_adds_only_the_questions_supplied_by_analysis() -> None:
    core = valid_core(questions=("سرور مقصد از قبل آماده است؟",)).model_copy(
        update={"question_reasons": ("آماده بودن سرور زمان استقرار را تغییر می‌دهد.",)}
    )

    draft = ProposalGenerator().build(
        project=project(),
        core=core,
        provider_name="ollama",
        model_name="local-model",
        prompt_version="proposal-core-v1",
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert "سرور مقصد از قبل آماده است؟" in draft.body
    assert draft.body.count("؟") == 1


def test_generator_preserves_complete_details_for_validator_instead_of_truncating() -> None:
    repeated = " ".join(f"واژه{i}" for i in range(80))
    core = ProposalCore(
        project_understanding=repeated,
        technical_approach=repeated,
        questions=(repeated,),
    )

    draft = ProposalGenerator().build(
        project=project(),
        core=core,
        provider_name="codex_cli",
        model_name="test-model",
        prompt_version="proposal-core-v1",
        generated_at=datetime(2026, 9, 14, tzinfo=UTC),
    )

    middle = draft.body.removeprefix(f"{FIXED_INTRO}\n\n").removesuffix(f"\n\n{FIXED_OUTRO}")
    assert repeated in middle
    assert len(middle.split()) > 100
    assert "…" not in middle


def test_questions_default_to_empty() -> None:
    core = ProposalCore(project_understanding="نیاز", technical_approach="روش")
    assert core.questions == ()


@pytest.mark.parametrize("reasons", ((), ("",), ("  ",), ("زمان استقرار", "هزینه سرور")))
def test_unjustified_or_mismatched_questions_are_suppressed(reasons: tuple[str, ...]) -> None:
    core = valid_core(questions=("سرور مقصد از قبل آماده است؟",)).model_copy(
        update={"question_reasons": reasons}
    )
    draft = ProposalGenerator().build(
        project=project(),
        core=core,
        provider_name="test",
        model_name="test",
        prompt_version="test",
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert "سرور مقصد" not in draft.body
    assert "؟" not in draft.body
