"""Provider-neutral contracts, prompts, retry policy, and safety gate."""

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
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
        try:
            output = await self._complete(
                messages=_analysis_messages(project, rules),
                schema=SemanticAnalysis,
            )
        except ProviderUnavailableError:
            return AnalysisResult(
                decision=AnalysisDecision.NEEDS_REVIEW,
                confidence=0,
                programming_deliverable=None,
                hard_negative=None,
                reason="LLM provider unavailable or returned invalid structured output",
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
                "Write only the project-specific middle of a Persian proposal. Be natural, "
                "precise, and concise. Keep project understanding and technical approach "
                "together within 45-70 Persian words. Include at most two genuinely necessary "
                "questions. "
                "Do not add greetings, biography, links, phone numbers, or messaging IDs."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    )
