# ruff: noqa: RUF001

from datetime import UTC, datetime
from decimal import Decimal

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
            "برای این ربات تلگرام، دریافت مطمئن پیام‌ها از طریق وب‌هوک، نگهداری اطلاعات "
            "کاربران و ثبت رویدادها در پایگاه داده مهم است تا سرویس بعد از قطعی یا راه‌اندازی "
            "دوباره هم بدون ازدست‌رفتن اطلاعات ادامه دهد."
        ),
        technical_approach=(
            "پیاده‌سازی را با پایتون و ساختار ماژولار انجام می‌دهم؛ مسیرهای وب‌هوک، منطق "
            "پردازش پیام و لایه دسترسی به PostgreSQL جدا می‌شوند و سناریوهای اصلی نیز تست "
            "خواهند شد. در پایان، تنظیمات اجرا و راهنمای استقرار را تحویل می‌دهم تا نگهداری "
            "و توسعه قابلیت‌های بعدی ربات ساده باشد."
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


def test_generator_preserves_the_user_approved_identity_and_portfolio_text() -> None:
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
        "تخصصی هوش مصنوعی از آمریکا. چند روزی است که در این سایت نیز پروژه می‌پذیرم. "
        "چند پروژه مشابه پروژه شما را قبلاً انجام داده‌ام و تقریباً به‌طور کامل متوجه نیاز "
        "و نتیجه موردنظرتان شده‌ام."
    )
    approved_outro = (
        "برای آشنایی بیشتر با سوابق و مشاهده تعدادی از پروژه‌هایم، لینک کانال یوتیوب، "
        "گیت‌هاب و لینکدین را خدمتتان قرار می‌دهم. همچنین می‌توانید نام من را در گوگل "
        "جست‌وجو کنید تا پروژه‌ها و سوابق بیشتری را که در سایت‌های مختلف منتشر شده‌اند "
        "مشاهده کنید.\n\n"
        "کانال یوتیوب:\nhttps://www.youtube.com/@matinkafashian_\n\n"
        "گیت‌هاب:\nhttps://github.com/matinkafashian\n\n"
        "لینکدین:\nhttps://www.linkedin.com/in/matinkafashian\n\n"
        "اگر سؤال دیگری بود، در خدمتم."
    )
    assert draft.body.startswith(f"{approved_intro}\n\n")
    assert draft.body.endswith(f"\n\n{approved_outro}")


def test_generator_adds_only_the_questions_supplied_by_analysis() -> None:
    core = valid_core(questions=("سرور مقصد از قبل آماده است؟",))

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
