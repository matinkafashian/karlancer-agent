# ruff: noqa: RUF001
"""Provider-neutral contracts, prompts, retry policy, and safety gate."""

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from karlancer_agent.classification.rules import RuleAssessment
from karlancer_agent.domain.enums import AnalysisDecision
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.llm.schemas import ProposalCore, SemanticAnalysis

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class ProviderUnavailableError(RuntimeError):
    """The provider failed safely before any external side effect."""


class ProviderQuotaError(RuntimeError):
    """The ChatGPT/Codex usage window is exhausted until ``retry_at``."""

    def __init__(self, message: str, *, retry_at: datetime | None) -> None:
        super().__init__(message)
        self.retry_at = retry_at


class ProviderAuthenticationError(RuntimeError):
    """The provider needs an explicit user sign-in and must not be retried."""


class LLMProvider(Protocol):
    """One contract for hosted OpenAI-compatible services and Ollama."""

    async def analyze(
        self, project: ExtractedProject, rules: RuleAssessment
    ) -> AnalysisResult: ...

    async def draft(
        self, project: ExtractedProject, analysis: AnalysisResult
    ) -> ProposalCore: ...


class StructuredLLMProvider(ABC):
    """Shared validation and the single-retry policy."""

    def __init__(self, *, model: str) -> None:
        self.model = model

    async def analyze(
        self, project: ExtractedProject, rules: RuleAssessment
    ) -> AnalysisResult:
        output = await self._complete(
            messages=_analysis_messages(project, rules),
            schema=SemanticAnalysis,
        )

        assert isinstance(output, SemanticAnalysis)
        decision = output.decision
        if decision is AnalysisDecision.ELIGIBLE and (
            output.confidence < 0.85 or output.hard_negative is not None
        ):
            decision = AnalysisDecision.NEEDS_REVIEW

        return AnalysisResult(
            decision=decision,
            confidence=output.confidence,
            category=output.category,
            programming_deliverable=output.programming_deliverable,
            matched_domains=output.matched_domains,
            hard_negative=output.hard_negative,
            ambiguities=output.ambiguities,
            recommended_questions=output.recommended_questions,
            reason="; ".join(output.reasons),
            technical_approach=output.technical_approach,
        )

    async def draft(
        self, project: ExtractedProject, analysis: AnalysisResult
    ) -> ProposalCore:
        output = await self._complete(
            messages=_draft_messages(project, analysis),
            schema=ProposalCore,
        )
        assert isinstance(output, ProposalCore)
        return output

    async def _complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        schema: type[StructuredModel],
    ) -> StructuredModel:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                raw = await self._request(messages=messages, schema=schema)
                return schema.model_validate_json(raw)
            except (
                httpx.TransportError,
                json.JSONDecodeError,
                ValidationError,
                ValueError,
            ) as error:
                last_error = error
        raise ProviderUnavailableError("provider failed after one retry") from last_error

    @abstractmethod
    async def _request(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        schema: type[StructuredModel],
    ) -> str:
        """Return a JSON string or raise before any side effect."""


def is_auto_eligible(
    analysis: AnalysisResult,
    *,
    extractor_valid: bool,
    proposal_valid: bool,
) -> bool:
    """Keep automatic submission behind explicit, deterministic conditions."""

    return (
        analysis.decision is AnalysisDecision.ELIGIBLE
        and analysis.confidence >= 0.85
        and analysis.hard_negative is None
        and extractor_valid
        and proposal_valid
    )


def _analysis_messages(
    project: ExtractedProject, rules: RuleAssessment
) -> tuple[dict[str, str], ...]:
    payload = {
        "project": project.model_dump(mode="json"),
        "deterministic_rules": rules.model_dump(mode="json"),
    }
    return (
        {
            "role": "system",
            "content": (
                "You classify freelance projects. Accept programming and educational work in "
                "Python, ML, DL, CV, NLP, LLM, RAG, agents, APIs, and messaging bots. Reject "
                "tool-only AI content, image, video, or character work. Do not guess missing facts."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    )


def _draft_messages(
    project: ExtractedProject, analysis: AnalysisResult
) -> tuple[dict[str, str], ...]:
    payload = {
        "project": project.model_dump(mode="json"),
        "analysis": analysis.model_dump(mode="json"),
    }
    return (
        {
            "role": "system",
            "content": (
                "Write complete, natural Persian sentences, never a noun-list opening such as "
                "'ربات تلگرام برای دریافت سفارش، ثبت مشخصات...'. Vary the opening with the task; "
                "do not repeatedly use one stock introduction. For example 'از توضیحات شما، هدف "
                "اصلی این است که...' is an option, not a required template. Describe the intended "
                "outcome briefly, then contribute a concrete technical decision, tradeoff or "
                "failure "
                "case and how to handle it. Do not merely rephrase the advertisement. Use ordinary "
                "professional Persian, not ornate or sales-heavy prose. Include a technology name "
                "only when justified by the description or a clearly stated dependency. "
                "For each question supply a corresponding entry in question_reasons explaining "
                "the exact "
                "unanswered ambiguity and its material effect on architecture, cost or time. "
                "If no such reason exists, omit the question. Do not ask general engagement "
                "questions. "
                "Write only the project-specific middle of a Persian proposal. Be natural, "
                "specific, and conversational rather than polished or sales-heavy. Adapt length "
                "to the actual scope: roughly 25-60 Persian words for simple work, 60-120 for "
                "moderate work, and 120-200 for complex work, across all fields combined. These "
                "are guidelines, not quotas: do not pad short proposals. A fixed price sentence "
                "is added later. Keep project_understanding brief; spend words on useful "
                "decisions. "
                "Separate explicit requirements from assumptions. Never invent platforms, "
                "features, "
                "deadlines or immediate availability. Qualify important inferences. "
                "Do not replace a necessary question with an assertion about what the client must "
                "mean. For example explain 'if by no API you mean no third-party service...' "
                "rather "
                "than claiming the client must mean this or that all alternatives are impossible. "
                "Explain at least "
                "one project-specific technical issue and how you would address it, not just a "
                "list "
                "of requirements or technology names. Prefer standard platform capabilities over "
                "custom automation when sufficient. Explain dependencies on version, hosting, "
                "access "
                "or plan only when relevant. Do not say 'appropriate API/library/architecture' "
                "without explaining the likely option and what determines it. Compare advertised "
                "budget, duration and scope; if clearly incompatible, propose a concrete reduced "
                "phase from the outset instead of promising everything then asking about an MVP. "
                "Never promise recovery of deleted data without establishing an available source; "
                "distinguish prior archiving from later recovery. Clarify 'without API' rather "
                "than "
                "assuming unofficial access. For data imports consider relationships, stable IDs, "
                "import order and validation before proposing a custom integration. "
                "Avoid generic assurances, inflated claims, "
                "and canned statements that merely say the requirements are understood. Include "
                "zero questions by default. Include at most two only when you are over 90 percent "
                "confident that a genuine unresolved ambiguity requires the employer's answer "
                "because it materially changes implementation, scope, schedule or price. This is "
                "a conservative decision rule, not a measured probability. Check the complete "
                "description before asking; never repeat an answered question or ask the employer "
                "to select a library you should select yourself. Return an empty questions "
                "list when "
                "the project is sufficiently clear; never invent a question to create engagement. "
                "Do not add greetings, biography, links, phone numbers, or messaging IDs."
                "\nبرای کار ساده، واقعاً کوتاه بنویس: برداشت در یک جمله و راهکار در یک یا دو "
                "جمله؛ حدود ۲۵ تا ۶۰ کلمه در مجموع. به‌طور پیش‌فرض کوتاه بنویس و فقط وقتی "
                "چند بخش مستقل یا ریسک تعیین‌کننده وجود دارد توضیح را بیشتر کن. تکرار آگهی، "
                "قول مستندسازی و تست عمومی را برای پرکردن متن اضافه نکن. مثلاً برای حذف "
                "تکراری CSV کافی است بگویی اولین ردیف و ترتیب حفظ می‌شود و با csv و یک set "
                "ردیف‌های تکراری حذف و خروجی در فایل جدا نوشته می‌شود. به تغییر فایل اصلی "
                "یا محدودیت تأییدنشده اشاره نکن. پیش از تحویل، فرض‌های بی‌پشتوانه و سؤال‌های "
                "غیرضروری را حذف کن."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    )
