from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from karlancer_agent.browser.extractor import ExtractionError
from karlancer_agent.browser.submitter import SubmissionResult
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.services.auto_cycle import AutoCycleService
from karlancer_agent.services.pipeline import PipelineRunResult, ProjectStageResult


class FakePipeline:
    async def ingest(self, *, idempotency_key: str) -> PipelineRunResult:
        raise AssertionError("automatic cycles must not run ingestion")

    async def analyze(self, project_id: UUID):
        raise AssertionError("no pending analysis expected")

    async def draft(self, project_id: UUID):
        raise AssertionError("no pending draft expected")


class FakeRepository:
    def __init__(self, candidate_ids: list[UUID]) -> None:
        self.candidate_ids = candidate_ids
        self.rows = {
            project_id: SimpleNamespace(
                id=project_id,
                title=f"project-{index}",
                canonical_project_url=f"https://www.karlancer.com/projects/{index}/test",
            )
            for index, project_id in enumerate(candidate_ids)
        }
        self.errors: list[tuple[UUID, str]] = []
        self.system_paused = False
        self.unavailable: list[tuple[UUID, str]] = []

    async def is_system_paused(self) -> bool:
        return self.system_paused

    async def pause_system(self, *, reason: str) -> bool:
        self.system_paused = True
        return True

    async def list_shadow_pending(self) -> tuple[UUID, ...]:
        return ()

    async def list_auto_submission_candidates(self) -> tuple[UUID, ...]:
        return tuple(self.candidate_ids)

    async def get_project(self, project_id: UUID):
        return self.rows.get(project_id)

    async def record_project_error(self, project_id: UUID, *, reason: str) -> bool:
        self.errors.append((project_id, reason))
        return True

    async def reject_unavailable_project(
        self, project_id: UUID, *, reason: str
    ) -> bool:
        self.unavailable.append((project_id, reason))
        return True


class FakeBrowser:
    @asynccontextmanager
    async def new_page(self):
        yield object()


class FakeSubmitter:
    def __init__(self, broken: set[UUID] | None = None) -> None:
        self.broken = broken or set()
        self.project_ids: list[UUID] = []

    async def submit(self, page: object, command):
        self.project_ids.append(command.project_id)
        if command.project_id in self.broken:
            return SubmissionResult(
                project_id=command.project_id,
                status="paused_error",
                state=ProjectState.PAUSED_ERROR,
                reason="login_required",
            )
        return SubmissionResult(
            project_id=command.project_id,
            status="submitted",
            state=ProjectState.SUBMITTED,
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.alerts = []

    async def queue_and_dispatch(self, alert) -> bool:
        self.alerts.append(alert)
        return True


@pytest.mark.asyncio
async def test_bootstrap_submits_only_ten_newest_eligible_unsubmitted_projects() -> None:
    newest = [uuid4() for _ in range(13)]
    repository = FakeRepository(newest)
    submitter = FakeSubmitter()
    notifier = FakeNotifier()
    service = AutoCycleService(
        pipeline=FakePipeline(),
        repository=repository,
        browser=FakeBrowser(),
        submitter=submitter,
        notifier=notifier,
    )

    result = await service.run_once(
        idempotency_key="bootstrap-001", bootstrap_limit=10
    )

    assert result.submitted == 10
    assert submitter.project_ids == newest[:10]
    assert result.discovered == 0
    assert notifier.alerts == []


@pytest.mark.asyncio
async def test_project_failure_is_aggregated_into_single_cycle_report() -> None:
    broken, good = uuid4(), uuid4()
    repository = FakeRepository([broken, good])
    submitter = FakeSubmitter({broken})
    notifier = FakeNotifier()
    service = AutoCycleService(
        pipeline=FakePipeline(),
        repository=repository,
        browser=FakeBrowser(),
        submitter=submitter,
        notifier=notifier,
    )

    result = await service.run_once(idempotency_key="cycle-002", bootstrap_limit=None)

    assert result.errors == 1
    assert result.submitted == 0
    assert submitter.project_ids == [broken]
    assert repository.errors[0][0] == broken
    assert len(notifier.alerts) == 1
    assert "ورود" in notifier.alerts[0].body
    assert "AI paused" not in notifier.alerts[0].body


@pytest.mark.asyncio
async def test_analysis_failure_pauses_cycle_and_later_calls_stay_silent() -> None:
    project_id = uuid4()
    repository = FakeRepository([])
    async def pending():
        return (project_id,)
    repository.list_shadow_pending = pending

    class BrokenAnalysis(FakePipeline):
        async def analyze(self, project_id):
            raise RuntimeError("provider unavailable")

    notifier = FakeNotifier()
    submitter = FakeSubmitter()
    service = AutoCycleService(
        pipeline=BrokenAnalysis(), repository=repository, browser=FakeBrowser(),
        submitter=submitter, notifier=notifier,
    )
    result = await service.run_once(idempotency_key="analysis-failure", bootstrap_limit=None)
    assert result.errors == 1
    assert repository.system_paused is True
    assert len(notifier.alerts) == 1
    assert submitter.project_ids == []
    await service.run_once(idempotency_key="later", bootstrap_limit=None)
    assert len(notifier.alerts) == 1


@pytest.mark.asyncio
async def test_missing_project_card_is_rejected_without_stopping_automation() -> None:
    unavailable, candidate = uuid4(), uuid4()
    repository = FakeRepository([candidate])
    repository.candidate_ids = []

    async def pending():
        return (unavailable, candidate)

    repository.list_shadow_pending = pending

    class UnavailablePipeline(FakePipeline):
        async def analyze(self, project_id):
            if project_id == unavailable:
                raise ExtractionError("Karlancer project card was not found")
            return ProjectStageResult(project_id=project_id, state=ProjectState.ELIGIBLE)

        async def draft(self, project_id):
            repository.candidate_ids.append(project_id)
            return ProjectStageResult(project_id=project_id, state=ProjectState.DRAFTED)

    notifier = FakeNotifier()
    submitter = FakeSubmitter()
    service = AutoCycleService(
        pipeline=UnavailablePipeline(), repository=repository,
        browser=FakeBrowser(), submitter=submitter, notifier=notifier,
    )

    result = await service.run_once(
        idempotency_key="stale-project-001", bootstrap_limit=1
    )

    assert result.rejected == 1
    assert result.errors == 0
    assert result.submitted == 1
    assert repository.system_paused is False
    assert repository.unavailable[0][0] == unavailable
    assert notifier.alerts == []


@pytest.mark.asyncio
async def test_bootstrap_limit_caps_attempts_even_when_submission_fails() -> None:
    broken, untouched = uuid4(), uuid4()
    repository = FakeRepository([broken, untouched])
    submitter = FakeSubmitter({broken})
    service = AutoCycleService(
        pipeline=FakePipeline(),
        repository=repository,
        browser=FakeBrowser(),
        submitter=submitter,
        notifier=FakeNotifier(),
    )

    result = await service.run_once(
        idempotency_key="single-attempt-001", bootstrap_limit=1
    )

    assert result.submitted == 0
    assert result.errors == 1
    assert submitter.project_ids == [broken]


@pytest.mark.asyncio
async def test_serious_submission_error_persistently_stops_later_cycles() -> None:
    broken, untouched = uuid4(), uuid4()
    repository = FakeRepository([broken, untouched])
    submitter = FakeSubmitter({broken})
    service = AutoCycleService(
        pipeline=FakePipeline(), repository=repository, browser=FakeBrowser(),
        submitter=submitter, notifier=FakeNotifier(),
    )

    first = await service.run_once(idempotency_key="fatal-001", bootstrap_limit=None)
    second = await service.run_once(idempotency_key="fatal-002", bootstrap_limit=None)

    assert first.errors == 1
    assert repository.system_paused is True
    assert submitter.project_ids == [broken]
    assert second.submitted == 0
    assert second.errors == 0


@pytest.mark.asyncio
async def test_quota_error_does_not_globally_pause_automation() -> None:
    from datetime import UTC, datetime, timedelta

    from karlancer_agent.services.provider_circuit import ProviderCoolingDown

    project_id = uuid4()
    repository = FakeRepository([])
    async def pending():
        return (project_id,)
    repository.list_shadow_pending = pending

    class QuotaPipeline(FakePipeline):
        calls = 0
        async def analyze(self, project_id):
            self.calls += 1
            raise ProviderCoolingDown(datetime.now(UTC) + timedelta(hours=5))

    pipeline = QuotaPipeline()
    submitter = FakeSubmitter()
    notifier = FakeNotifier()
    service = AutoCycleService(
        pipeline=pipeline, repository=repository, browser=FakeBrowser(),
        submitter=submitter, notifier=notifier,
    )
    await service.run_once(idempotency_key="quota-stop-1", bootstrap_limit=None)
    await service.run_once(idempotency_key="quota-stop-2", bootstrap_limit=None)
    assert pipeline.calls == 2
    assert repository.system_paused is False
    assert submitter.project_ids == []
    assert notifier.alerts == []


@pytest.mark.asyncio
async def test_prepares_only_one_draft_before_submission() -> None:
    ids = [uuid4(), uuid4(), uuid4()]
    repository = FakeRepository(ids.copy())
    repository.candidate_ids = []
    events = []
    async def pending():
        return tuple(ids)
    repository.list_shadow_pending = pending

    class RecordingPipeline(FakePipeline):
        async def analyze(self, project_id):
            events.append(("analyze", project_id))
            return ProjectStageResult(project_id=project_id, state=ProjectState.ELIGIBLE)
        async def draft(self, project_id):
            events.append(("draft", project_id))
            repository.candidate_ids.append(project_id)
            return ProjectStageResult(project_id=project_id, state=ProjectState.DRAFTED)

    submitter = FakeSubmitter()
    service = AutoCycleService(
        pipeline=RecordingPipeline(), repository=repository, browser=FakeBrowser(),
        submitter=submitter, notifier=FakeNotifier(),
    )
    result = await service.run_once(idempotency_key="bounded-work", bootstrap_limit=None)
    assert events == [("analyze", ids[0]), ("draft", ids[0])]
    assert submitter.project_ids == [ids[0]]
    assert result.submitted == 1


@pytest.mark.asyncio
async def test_ready_candidate_skips_pending_model_work() -> None:
    project_id = uuid4()
    repository = FakeRepository([project_id])
    async def pending():
        raise AssertionError("ready proposal should be submitted first")
    repository.list_shadow_pending = pending
    service = AutoCycleService(
        pipeline=FakePipeline(), repository=repository, browser=FakeBrowser(),
        submitter=FakeSubmitter(), notifier=FakeNotifier(),
    )
    result = await service.run_once(idempotency_key="reuse-draft", bootstrap_limit=None)
    assert result.submitted == 1
