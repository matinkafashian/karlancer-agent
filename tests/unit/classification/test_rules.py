from dataclasses import dataclass

import pytest

from karlancer_agent.classification.rules import RuleDecision, RulesClassifier


@dataclass(frozen=True)
class Case:
    title: str
    tags: tuple[str, ...]
    description: str
    expected: RuleDecision


CASES = [
    Case("Python automation script", ("Python",), "Automate reports", RuleDecision.ACCEPT),
    Case("اسکریپت پایتون", ("اتوماسیون",), "خروجی کدنویسی", RuleDecision.ACCEPT),
    Case("REST API with Python", ("backend",), "Build endpoints", RuleDecision.ACCEPT),
    Case("Web scraping service", ("scraping",), "Crawler in Python", RuleDecision.ACCEPT),
    Case("Machine learning model", ("ML",), "Train and evaluate", RuleDecision.ACCEPT),
    Case("مدل یادگیری ماشین", (), "آموزش و ارزیابی مدل", RuleDecision.ACCEPT),
    Case("Data science analysis", ("pandas",), "Notebook and code", RuleDecision.ACCEPT),
    Case("Deep learning pipeline", ("PyTorch",), "Neural model", RuleDecision.ACCEPT),
    Case("شبکه عصبی", ("یادگیری عمیق",), "پیاده‌سازی مدل", RuleDecision.ACCEPT),
    Case("Computer vision system", ("OpenCV",), "Image processing code", RuleDecision.ACCEPT),
    Case("سامانه بینایی ماشین", (), "تشخیص اشیا", RuleDecision.ACCEPT),
    Case("Object detection with YOLO", (), "Python inference", RuleDecision.ACCEPT),
    Case("NLP classification", (), "Text classifier", RuleDecision.ACCEPT),
    Case("پردازش زبان طبیعی", (), "طبقه‌بندی متن", RuleDecision.ACCEPT),
    Case("Speech recognition model", (), "Transcription backend", RuleDecision.ACCEPT),
    Case("LLM application", (), "Structured generation", RuleDecision.ACCEPT),
    Case("Large language model evaluation", (), "Python code", RuleDecision.ACCEPT),
    Case("RAG chatbot", (), "Retrieval and citations", RuleDecision.ACCEPT),
    Case("Vector database integration", ("Qdrant",), "Search API", RuleDecision.ACCEPT),
    Case("AI agent workflow", ("agentic",), "Tool calling", RuleDecision.ACCEPT),
    Case("ساخت عامل هوشمند", (), "ابزارهای چندمرحله‌ای", RuleDecision.ACCEPT),
    Case("MLOps deployment", (), "Model serving and monitoring", RuleDecision.ACCEPT),
    Case("Telegram bot in Python", (), "Webhook and database", RuleDecision.ACCEPT),
    Case("ربات تلگرام", ("پایتون",), "اتصال به API", RuleDecision.ACCEPT),
    Case("ربات بله", (), "پیاده‌سازی پیام‌رسان", RuleDecision.ACCEPT),
    Case("ربات روبیکا", (), "backend service", RuleDecision.ACCEPT),
    Case("WhatsApp bot", (), "Business API integration", RuleDecision.ACCEPT),
    Case("Discord bot", (), "Python commands", RuleDecision.ACCEPT),
    Case("آموزش پایتون", ("آموزشی",), "جلسات عملی برنامه‌نویسی", RuleDecision.ACCEPT),
    Case("آموزش یادگیری ماشین", (), "تدریس پروژه‌محور", RuleDecision.ACCEPT),
    Case("AI", (), "Need help with AI", RuleDecision.REJECT),
    Case("هوش مصنوعی", (), "یک پروژه در زمینه هوش مصنوعی", RuleDecision.REJECT),
    Case("ادیت ویدئو با هوش مصنوعی", (), "کار با ابزار آماده", RuleDecision.REJECT),
    Case("ساخت کاراکتر با AI", (), "بدون برنامه‌نویسی", RuleDecision.REJECT),
    Case("تولید محتوا با ChatGPT", (), "اپراتوری ابزار", RuleDecision.REJECT),
    Case("ترجمه مقاله", (), "ترجمه و ویراستاری", RuleDecision.REJECT),
    Case("ورود اطلاعات", ("data entry",), "ثبت دستی محصول", RuleDecision.REJECT),
    Case("annotation دستی تصاویر", (), "برچسب‌گذاری دستی", RuleDecision.REJECT),
    Case("طراحی تصویر در فتوشاپ", (), "طراحی گرافیکی", RuleDecision.REJECT),
    Case("ادمین تلگرام", (), "پاسخ‌گویی به کاربران", RuleDecision.REJECT),
    Case("استفاده از API", (), "جزئیات فنی مشخص نیست", RuleDecision.NEEDS_LLM),
    Case(
        "ادیت ویدئو با Python و FFmpeg",
        (),
        "توسعه اسکریپت پردازش ویدئو",
        RuleDecision.NEEDS_LLM,
    ),
    Case(
        "ساخت API تولید تصویر",
        ("Python",),
        "توسعه نرم‌افزار و اتصال مدل",
        RuleDecision.NEEDS_LLM,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.title)
def test_bilingual_decision_matrix(case: Case) -> None:
    result = RulesClassifier.default().assess(
        title=case.title,
        tags=case.tags,
        description=case.description,
    )

    assert result.decision is case.expected


def test_ai_alone_never_appears_as_a_matched_term() -> None:
    result = RulesClassifier.default().assess(
        title="AI project",
        tags=("هوش مصنوعی",),
        description="AI AI AI",
    )

    assert result.matched_terms == frozenset()
    assert result.score == 0


def test_title_tags_and_description_have_descending_weights() -> None:
    classifier = RulesClassifier.default()

    title = classifier.assess(title="Python", tags=(), description="unrelated")
    tag = classifier.assess(title="unrelated", tags=("Python",), description="unrelated")
    description = classifier.assess(title="unrelated", tags=(), description="Python")

    assert title.score > tag.score > description.score


def test_conflicting_tool_and_programming_signals_require_semantic_review() -> None:
    result = RulesClassifier.default().assess(
        title="ادیت ویدئو با Python و FFmpeg",
        tags=("ادیت ویدئو", "Python"),
        description="یک اسکریپت پردازش ویدئو بسازید",
    )

    assert result.decision is RuleDecision.NEEDS_LLM
    assert result.excluded_terms
    assert "python" in result.matched_terms


def test_glossary_version_is_recorded() -> None:
    result = RulesClassifier.default().assess(title="RAG service", tags=(), description="Python")

    assert result.glossary_version == "1.0.0"


@pytest.mark.parametrize(
    "title,description,expected",
    (
        ("آموزش هوش مصنوعی", "جلسات عملی برای دانشجویان", RuleDecision.ACCEPT),
        ("Artificial intelligence training", "Practical lessons", RuleDecision.ACCEPT),
        ("ساخت API تولید تصویر", "اتصال به سرویس مدل", RuleDecision.NEEDS_LLM),
        ("تولید تصویر", "کار با ابزار آماده", RuleDecision.REJECT),
        ("تولید محتوا با هوش مصنوعی", "بدون کدنویسی", RuleDecision.REJECT),
    ),
)
def test_education_and_image_api_are_not_confused_with_tool_operation(
    title: str,
    description: str,
    expected: RuleDecision,
) -> None:
    assert (
        RulesClassifier.default().assess(title=title, tags=(), description=description).decision
        is expected
    )
