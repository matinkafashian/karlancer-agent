"""Resume-safe read/analyze/draft pipeline with no submission capability."""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from karlancer_agent.browser.extractor import PageLike, ProjectExtractor
from karlancer_agent.classification.rules import RuleDecision, RulesClassifier
from karlancer_agent.db.models import ProjectRow
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.enums import AnalysisDecision, ProjectState
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.ingestion.base import ProjectSource
from karlancer_agent.llm.base import LLMProvider
from karlancer_agent.proposal.generator import ProposalGenerator
from karlancer_agent.proposal.validator import ProposalValidator

logger = logging.getLogger(__name__)
SOURCE_NAME = "karlancer_projects"
ANALYSIS_VERSION = "semantic-v1"
PROMPT_VERSION = "proposal-core-v3-natural"


class BrowserPages(Protocol):
    def new_page(self) -> AbstractAsyncContextManager[PageLike]: ...


class PipelineRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    discovered: int = Field(ge=0)
    drafted: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    rejected: int = Field(ge=0)


class ProjectStageResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: UUID
    state: ProjectState
    skipped: bool = False


class ShadowPipeline:
    """Own discovery through validated draft; it cannot perform submissions."""

    def __init__(
        self,
        *,
        source: ProjectSource,
        repository: ProjectRepository,
        browser: BrowserPages,
        classifier: RulesClassifier,
        provider: LLMProvider,
        provider_name: str,
        model_name: str,
        proposal_generator: ProposalGenerator,
        proposal_validator: ProposalValidator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._repository = repository
        self._browser = browser
        self._classifier = classifier
        self._provider = provider
        self._provider_name = provider_name
        self._model_name = model_name
        self._proposal_generator = proposal_generator
        self._proposal_validator = proposal_validator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def ingest(self, *, idempotency_key: str) -> PipelineRunResult:
        created_ids = await self._ingest_projects(idempotency_key=idempotency_key)
        return PipelineRunResult(
            discovered=len(created_ids), drafted=0, needs_review=0, rejected=0
        )

    async def run_once(self, *, idempotency_key: str) -> PipelineRunResult:
        """Run one cycle over new projects and unfinished work from earlier cycles."""

        created_ids = await self._ingest_projects(idempotency_key=idempotency_key)
        project_ids = await self._repository.list_shadow_pending()
        drafted = needs_review = rejected = 0
        for project_id in project_ids:
            analyzed = await self.analyze(project_id)
            if analyzed.state is ProjectState.REJECTED:
                rejected += 1
                continue
            if analyzed.state is ProjectState.NEEDS_REVIEW:
                needs_review += 1
                continue
            drafted_result = await self.draft(project_id)
            if drafted_result.state is ProjectState.DRAFTED:
                drafted += 1
            elif drafted_result.state is ProjectState.NEEDS_REVIEW:
                needs_review += 1
        return PipelineRunResult(
            discovered=len(created_ids),
            drafted=drafted,
            needs_review=needs_review,
            rejected=rejected,
        )

    async def analyze(self, project_id: UUID) -> ProjectStageResult:
        """Resume extraction if needed, then persist exactly one analysis."""

        row = await self._required_project(project_id)
        state = ProjectState(row.state)
        if state is ProjectState.DISCOVERED:
            await self._repository.claim_for_processing(project_id)
            row = await self._required_project(project_id)
            state = ProjectState(row.state)
        if state is ProjectState.EXTRACT_PENDING:
            async with self._browser.new_page() as page:
                extracted = await ProjectExtractor().extract(
                    page, row.canonical_project_url
                )
            await self._repository.record_extraction(project_id, extracted)
            row = await self._required_project(project_id)
            state = ProjectState(row.state)
        if state is not ProjectState.EXTRACTED:
            return ProjectStageResult(project_id=project_id, state=state, skipped=True)

        project = ExtractedProject.model_validate(row.extracted_data)
        rules = self._classifier.assess(
            title=project.title,
            tags=project.skills,
            description=project.description,
        )
        if rules.decision is RuleDecision.REJECT:
            analysis = AnalysisResult(
                decision=AnalysisDecision.REJECTED,
                confidence=1,
                programming_deliverable=None,
                matched_domains=tuple(sorted(rules.positive_categories)),
                hard_negative=", ".join(sorted(rules.excluded_terms)) or None,
                reason="deterministic rules rejected the project",
            )
            provider_name, model_name = "rules", "none"
        else:
            analysis = await self._provider.analyze(project, rules)
            provider_name, model_name = self._provider_name, self._model_name
        recorded = await self._repository.record_analysis(
            project_id,
            rules=rules,
            analysis=analysis,
            provider=provider_name,
            model_name=model_name,
            analysis_version=ANALYSIS_VERSION,
        )
        if not recorded:
            current = await self._required_project(project_id)
            return ProjectStageResult(
                project_id=project_id,
                state=ProjectState(current.state),
                skipped=True,
            )
        logger.info(
            "pipeline analysis completed",
            extra={"event": "analysis_completed", "project_id": str(project_id)},
        )
        return ProjectStageResult(
            project_id=project_id,
            state=_state_for_decision(analysis.decision),
        )

    async def draft(self, project_id: UUID) -> ProjectStageResult:
        """Generate, validate, and persist a draft only for an eligible project."""

        row = await self._required_project(project_id)
        state = ProjectState(row.state)
        if state is not ProjectState.ELIGIBLE:
            return ProjectStageResult(project_id=project_id, state=state, skipped=True)
        analysis = await self._repository.latest_analysis(project_id)
        if analysis is None:
            raise RuntimeError("eligible project has no persisted analysis")
        project = ExtractedProject.model_validate(row.extracted_data)
        core = await self._provider.draft(project, analysis)
        draft = self._proposal_generator.build(
            project=project,
            core=core,
            provider_name=self._provider_name,
            model_name=self._model_name,
            prompt_version=PROMPT_VERSION,
            generated_at=self._clock(),
        )
        validation = self._proposal_validator.validate(body=draft.body, project=project)
        recorded = await self._repository.record_draft(
            project_id, draft=draft, validation=validation
        )
        if not recorded:
            current = await self._required_project(project_id)
            return ProjectStageResult(
                project_id=project_id,
                state=ProjectState(current.state),
                skipped=True,
            )
        target = ProjectState.DRAFTED if validation.is_valid else ProjectState.NEEDS_REVIEW
        logger.info(
            "pipeline draft completed",
            extra={"event": "draft_completed", "project_id": str(project_id)},
        )
        return ProjectStageResult(project_id=project_id, state=target)

    async def _ingest_projects(self, *, idempotency_key: str) -> tuple[UUID, ...]:
        cursor = await self._repository.source_cursor(SOURCE_NAME)
        projects = await self._source.poll(cursor)
        created_ids: list[UUID] = []
        for project in projects:
            row, created = await self._repository.upsert_discovered(project)
            if created:
                created_ids.append(row.id)
        if projects:
            await self._repository.advance_source_cursor(
                SOURCE_NAME, max(item.channel_message_id for item in projects)
            )
        logger.info(
            "pipeline ingestion completed",
            extra={
                "event": "ingestion_completed",
                "discovered": len(created_ids),
            },
        )
        return tuple(created_ids)

    async def _required_project(self, project_id: UUID) -> ProjectRow:
        row = await self._repository.get_project(project_id)
        if row is None:
            raise LookupError(f"project {project_id} was not found")
        return row


def _state_for_decision(decision: AnalysisDecision) -> ProjectState:
    return {
        AnalysisDecision.ELIGIBLE: ProjectState.ELIGIBLE,
        AnalysisDecision.REJECTED: ProjectState.REJECTED,
        AnalysisDecision.NEEDS_REVIEW: ProjectState.NEEDS_REVIEW,
    }[decision]
