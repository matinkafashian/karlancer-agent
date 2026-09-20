"""Hard, model-independent validation before a draft may advance."""

# ruff: noqa: RUF001

import re
import unicodedata

from pydantic import BaseModel, ConfigDict

from karlancer_agent.classification.rules import normalize_text
from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.proposal.templates import FIXED_INTRO, FIXED_OUTRO, PRICE_SENTENCE

MIN_MIDDLE_WORDS = 15
MAX_MIDDLE_WORDS = 230

BANNED_CLICHES = (
    "پروژه کاملاً شدنی است",
    "پروژه‌تان را کاملاً بررسی کردم",
    "پروژه تون رو کاملا بررسی کردم",
    "صددرصد تضمینی",
    "صد در صد تضمینی",
    "بهترین کیفیت",
    "چند پروژه مشابه پروژه شما را قبلاً انجام داده‌ام",
    "تضمین صددرصد",
    "تضمین صد درصد",
    "تقریباً به‌طور کامل متوجه نیاز و نتیجه موردنظرتان شده‌ام",
    "تقریبا به طور کامل متوجه نیاز و نتیجه مورد نظرتان شده ام",
)

STOP_WORDS = frozenset(
    {
        "با",
        "برای",
        "این",
        "یک",
        "ساخت",
        "انجام",
        "پروژه",
        "و",
        "در",
        "از",
        "به",
        "the",
        "a",
        "an",
        "with",
        "build",
    }
)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str


class ProposalValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    is_valid: bool
    issues: tuple[ValidationIssue, ...]
    middle_word_count: int


class ProposalValidator:
    """Reject unsafe or generic text without asking a model to police itself."""

    def validate(self, *, body: str, project: ExtractedProject) -> ProposalValidation:
        issues: list[ValidationIssue] = []
        intro_valid = body.startswith(f"{FIXED_INTRO}\n\n")
        outro_valid = body.endswith(f"\n\n{FIXED_OUTRO}")
        if not intro_valid:
            issues.append(_issue("fixed_intro", "متن ثابت آغاز ناقص یا تغییرکرده است"))
        if not outro_valid:
            issues.append(
                _issue(
                    "fixed_outro",
                    "متن ثابت پایان تغییر کرده است",
                )
            )

        middle = _extract_middle(body, intro_valid=intro_valid, outro_valid=outro_valid)
        middle_word_count = len(_words(middle))
        if len(_words(middle.replace(PRICE_SENTENCE, ""))) < MIN_MIDDLE_WORDS:
            issues.append(_issue("middle_too_short", "متن اختصاصی بیش از حد کوتاه است"))
        if middle_word_count > MAX_MIDDLE_WORDS:
            issues.append(_issue("middle_too_long", "متن اختصاصی بیش از حد طولانی است"))

        if middle.count(PRICE_SENTENCE) != 1:
            issues.append(_issue("price_sentence", "جمله مصوب مبلغ و زمان وجود ندارد"))
        if not _has_required_middle_structure(middle):
            issues.append(
                _issue(
                    "middle_structure",
                    "برداشت از نیاز و مسیر فنی باید هر دو توضیح کافی داشته باشند",
                )
            )
        first_paragraph = middle.split("\n\n", 1)[0]
        if _is_nominal_list_opening(first_paragraph):
            issues.append(
                _issue("nominal_opening", "برداشت از پروژه باید جملهٔ کامل باشد، نه فهرست نیازها")
            )
        if len(re.findall(r"[؟?]", middle)) > 2:
            issues.append(_issue("too_many_questions", "بیش از دو سؤال در پیشنهاد آمده است"))
        normalized_middle = normalize_text(middle)
        if any(normalize_text(item) in normalized_middle for item in BANNED_CLICHES):
            issues.append(_issue("banned_cliche", "عبارت کلیشه‌ای ممنوع پیدا شد"))
        if _contains_off_platform_contact(body):
            issues.append(
                _issue(
                    "off_platform_contact",
                    "لینک، اطلاعات تماس یا ارجاع به سابقه خارج از سایت پیدا شد",
                )
            )
        if _has_repeated_paragraph(middle):
            issues.append(_issue("repeated_content", "بخشی از متن پیشنهاد تکرار شده است"))
        if not _is_project_relevant(middle, project):
            issues.append(_issue("project_relevance", "متن به جزئیات واقعی پروژه اشاره ندارد"))

        return ProposalValidation(
            is_valid=not issues,
            issues=tuple(issues),
            middle_word_count=middle_word_count,
        )


def _issue(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message)


def _extract_middle(body: str, *, intro_valid: bool, outro_valid: bool) -> str:
    start = len(FIXED_INTRO) + 2 if intro_valid else 0
    end = -(len(FIXED_OUTRO) + 2) if outro_valid else len(body)
    return body[start:end].strip()


def _words(value: str) -> list[str]:
    return re.findall(r"[\w+#.]+", value, flags=re.UNICODE)


def _contains_off_platform_contact(value: str) -> bool:
    normalized = (
        unicodedata.normalize("NFKC", value)
        .lower()
        .translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    )
    patterns = (
        r"(?<!\d)(?:\+?\d[\s\-().]*){8,15}(?!\d)",
        r"(?<!\d)(?:\+?98|0)?[\s\-().]*9(?:[\s\-().]*\d){9}(?!\d)",
        r"(?<![\w/])@[a-z0-9_]{4,}",
        r"\bt\s*[.‌]\s*me\s*(?:/|\s)",
        r"\btelegram\s*[.]\s*me\s*/",
        r"\bwa\s*[.]\s*me\s*/",
        r"https?\s*:\s*/\s*/|\bwww\s*[.]",
        r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|ir|io|me|dev|ai)(?:/|\b)",
        r"\b(?:youtube|github|linkedin)\b",
        r"(?:یوتیوب|گیت[‌\s-]*هاب|لینکدین)",
        r"نام\s+من.{0,30}گوگل.{0,30}جست",
        r"(?:آیدی|شناسه)\s*(?:تلگرام|telegram)",
        r"شماره\s*(?:تماس|تلفن|موبایل)",
        r"(?:در|از\s+طریق)\s+(?:تلگرام|telegram|واتساپ|whatsapp)\s+"
        r"(?:پیام|صحبت|ارتباط|هماهنگ)",
        r"(?:ارتباط|هماهنگی|صحبت)\s+خارج\s+از\s+سایت",
    )
    return any(re.search(pattern, normalized, flags=re.UNICODE) for pattern in patterns)


def _has_repeated_paragraph(value: str) -> bool:
    paragraphs = [
        normalize_text(item)
        for item in re.split(r"\n\s*\n", value)
        if len(_words(item)) >= 5 and item.strip() != PRICE_SENTENCE
    ]
    return len(paragraphs) != len(set(paragraphs))


def _has_required_middle_structure(value: str) -> bool:
    before_price = value.split(PRICE_SENTENCE, maxsplit=1)[0]
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", before_price) if item.strip()]
    return len(paragraphs) >= 2 and len(_words(before_price)) >= 10


def _is_project_relevant(value: str, project: ExtractedProject) -> bool:
    project_terms = set(
        _meaningful_terms(f"{project.title} {project.description} {' '.join(project.skills)}")
    )
    middle_terms = set(_meaningful_terms(value))
    return bool(project_terms & middle_terms)


def _meaningful_terms(value: str) -> tuple[str, ...]:
    return tuple(
        word for word in normalize_text(value).split() if len(word) >= 3 and word not in STOP_WORDS
    )


def _is_nominal_list_opening(value: str) -> bool:
    """Catch a narrow repeated task-list pattern, not general Persian grammar.

    Unknown sentence forms are left to semantic review. A list headed by
    'for receiving ..., recording ..., sending ...' is a known regression.
    """
    first_sentence = re.split(r"[.!؟?\n]", value, maxsplit=1)[0]
    normalized = normalize_text(first_sentence)
    if not re.search(r"\bبرای\s+(?:دریافت|ثبت|ارسال|ذخیره|مدیریت)\b", normalized):
        return False
    if not re.search(r"[،,]\s*(?:ثبت|ارسال|ذخیره|مدیریت|دریافت)\b", first_sentence):
        return False
    # Explicit clause markers indicate prose, including verbs outside any allowlist.
    if re.search(r"\b(?:را|که|باید|تا|می|نمی|است|هست|باشد|شود)\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(?:ثبت|ارسال|ذخیره|مدیریت|دریافت)\s+(?:مشخصات|اطلاعات|سفارش|پیام|داده)"
            r"(?:\s+(?:مشتری|کاربر|کاربران|به|برای|مدیر|در|پایگاه|داده))*$",
            normalized,
        )
    )
