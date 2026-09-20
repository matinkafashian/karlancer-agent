"""Safe provider used until model credentials are configured."""

from karlancer_agent.classification.rules import RuleAssessment
from karlancer_agent.domain.enums import AnalysisDecision
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.llm.base import ProviderUnavailableError
from karlancer_agent.llm.schemas import ProposalCore


class DisabledProvider:
    """Prevent model-dependent work without making ingestion fail."""

    async def analyze(
        self, project: ExtractedProject, rules: RuleAssessment
    ) -> AnalysisResult:
        return AnalysisResult(
            decision=AnalysisDecision.NEEDS_REVIEW,
            confidence=0,
            programming_deliverable=None,
            hard_negative=None,
            reason="LLM provider is disabled",
        )

    async def draft(
        self, project: ExtractedProject, analysis: AnalysisResult
    ) -> ProposalCore:
        raise ProviderUnavailableError("LLM provider is disabled")
