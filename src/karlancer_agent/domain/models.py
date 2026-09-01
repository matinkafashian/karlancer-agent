"""Immutable data passed between pipeline stages."""

from decimal import Decimal
from typing import Self

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from karlancer_agent.domain.enums import AnalysisDecision


class DomainModel(BaseModel):
    """Base class that prevents mutation between pipeline stages."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChannelProject(DomainModel):
    """Project metadata extracted from a public Telegram channel message."""

    channel_message_id: int = Field(gt=0)
    title: str = Field(min_length=1)
    budget_text: str = Field(min_length=1)
    duration_text: str = Field(min_length=1)
    hashtags: tuple[str, ...] = ()
    project_url: AnyHttpUrl


class ExtractedProject(DomainModel):
    """Validated fields read from the authenticated Karlancer project page."""

    project_id: str = Field(min_length=1)
    canonical_url: AnyHttpUrl
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    skills: tuple[str, ...] = ()
    budget_text: str = Field(min_length=1)
    budget_min: Decimal = Field(ge=0)
    budget_max: Decimal = Field(ge=0)
    currency: str = Field(min_length=1)
    duration_text: str = Field(min_length=1)
    duration_value: Decimal = Field(gt=0)
    duration_unit: str = Field(min_length=1)
    is_open: bool
    already_submitted: bool = False
    employer_display_id: str | None = None
    proposal_form_url: AnyHttpUrl

    @model_validator(mode="after")
    def validate_budget_range(self) -> Self:
        """Reject inverted ranges before a proposal can reach submission."""

        if self.budget_max < self.budget_min:
            raise ValueError("budget_max must be greater than or equal to budget_min")
        return self


class AnalysisResult(DomainModel):
    """Structured, auditable output from rules and semantic analysis."""

    decision: AnalysisDecision
    confidence: float = Field(ge=0, le=1)
    programming_deliverable: str | None
    matched_domains: tuple[str, ...] = ()
    hard_negative: str | None
    ambiguities: tuple[str, ...] = ()
    recommended_questions: tuple[str, ...] = Field(default=(), max_length=2)
    reason: str = Field(min_length=1)


class ProposalDraft(DomainModel):
    """Versioned proposal text produced before assisted or automatic submission."""

    project_id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    generated_at: AwareDatetime


class SubmissionReceipt(DomainModel):
    """Evidence captured only after Karlancer confirms a submission."""

    project_id: str = Field(min_length=1)
    submitted_at: AwareDatetime
    conversation_url: AnyHttpUrl | None = None
    evidence_hash: str = Field(min_length=1)
    form_budget: Decimal = Field(ge=0)
    duration_value: Decimal = Field(gt=0)
    duration_unit: str = Field(min_length=1)


class FirstReply(DomainModel):
    """First employer-authored message observed after submission."""

    project_id: str = Field(min_length=1)
    employer_message_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    received_at: AwareDatetime
    conversation_url: AnyHttpUrl
