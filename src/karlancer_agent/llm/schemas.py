"""Strict provider outputs shared by cloud and local models."""

from pydantic import BaseModel, ConfigDict, Field

from karlancer_agent.domain.enums import AnalysisDecision


class StructuredOutput(BaseModel):
    """Forbid invented fields so provider drift becomes a reviewable failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SemanticAnalysis(StructuredOutput):
    """Semantic project assessment returned as JSON, never free-form text."""

    category: str = Field(min_length=1)
    decision: AnalysisDecision
    confidence: float = Field(ge=0, le=1)
    reasons: tuple[str, ...] = Field(min_length=1)
    technical_approach: str = Field(min_length=1)
    programming_deliverable: str | None
    matched_domains: tuple[str, ...]
    hard_negative: str | None
    ambiguities: tuple[str, ...]
    recommended_questions: tuple[str, ...] = Field(max_length=2)


class ProposalCore(StructuredOutput):
    """Only the project-specific middle; fixed identity text is added later."""

    project_understanding: str = Field(min_length=1)
    technical_approach: str = Field(min_length=1)
    questions: tuple[str, ...] = Field(max_length=2)

