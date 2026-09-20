# ruff: noqa: RUF001

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from inspect import signature
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from karlancer_agent.classification.rules import RulesClassifier
from karlancer_agent.db.base import Base
from karlancer_agent.db.models import AnalysisRow, ProjectRow, ProposalDraftRow, SubmissionRow
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.enums import AnalysisDecision, ProjectState
from karlancer_agent.domain.models import AnalysisResult, ChannelProject
from karlancer_agent.llm.schemas import ProposalCore
from karlancer_agent.proposal.generator import ProposalGenerator
from karlancer_agent.proposal.validator import ProposalValidator
from karlancer_agent.services.pipeline import ShadowPipeline

FIXTURE = Path("tests/fixtures/project_ai_programming.html")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    value = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with value.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield value
    await value.dispose()


class StaticSource:
    async def poll(self, after_message_id: int | None) -> list[ChannelProject]:
        return [
            ChannelProject(
                channel_message_id=9001,
                title="استخراج محصول غذایی از فروشگاه‌های آنلاین",
                budget_text="۸۰۰,۰۰۰ تا ۳,۰۰۰,۰۰۰ تومان",
                duration_text="۱ روز",
                hashtags=("#Python", "#WebScraping"),
                project_url="https://www.karlancer.com/projects/food-scraper-2w6o5o0r46ne",
            )
        ]


class FixturePage:
    url = "https://www.karlancer.com/projects/food-scraper-2w6o5o0r46ne"

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.url = url

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        return None

    async def content(self) -> str:
        return FIXTURE.read_text(encoding="utf-8")


class FixtureBrowser:
    @asynccontextmanager
    async def new_page(self) -> AsyncIterator[FixturePage]:
        yield FixturePage()


class DeterministicProvider:
    analyze_calls = 0
    draft_calls = 0

    async def analyze(self, project, rules) -> AnalysisResult:
        self.analyze_calls += 1
        return AnalysisResult(
            decision=AnalysisDecision.ELIGIBLE,
            confidence=0.96,
            category="python_automation",
            programming_deliverable="اسکریپت استخراج محصول",
            matched_domains=("python", "automation"),
            hard_negative=None,
            reason="خروجی پروژه نرم‌افزار پایتون است",
            technical_approach="استخراج ساختاریافته و ذخیره قابل‌تکرار",
        )

    async def draft(self, project, analysis) -> ProposalCore:
        self.draft_calls += 1
        return ProposalCore(
            project_understanding=(
                "برای این پروژه، اطلاعات محصولات غذایی باید از فروشگاه‌های آنلاین استخراج "
                "و با ساختاری یکسان ذخیره شود تا داده‌های ناقص یا تکراری وارد خروجی نشوند."
            ),
            technical_approach=(
                "کار را با پایتون انجام می‌دهم؛ استخراج، پاک‌سازی و ذخیره داده‌ها جدا خواهند "
                "بود. کنترل خطا، تست نمونه و راهنمای اجرای مجدد نیز همراه کد تحویل می‌شود."
            ),
            questions=(),
        )


@pytest.mark.asyncio
async def test_shadow_pipeline_is_end_to_end_resume_safe_and_never_submits(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ProjectRepository(sessions)
    provider = DeterministicProvider()
    pipeline = ShadowPipeline(
        source=StaticSource(),
        repository=repository,
        browser=FixtureBrowser(),
        classifier=RulesClassifier.default(),
        provider=provider,
        provider_name="test-provider",
        model_name="test-model",
        proposal_generator=ProposalGenerator(),
        proposal_validator=ProposalValidator(),
    )

    ingested = await pipeline.ingest(idempotency_key="shadow-run-1")
    resumed = await pipeline.run_once(idempotency_key="shadow-run-1")
    second = await pipeline.run_once(idempotency_key="shadow-run-1")

    assert ingested.discovered == 1
    assert resumed.discovered == 0
    assert resumed.drafted == 1
    assert second.discovered == 0
    assert second.drafted == 0
    assert provider.analyze_calls == 1
    assert provider.draft_calls == 1
    assert "submitter" not in signature(ShadowPipeline).parameters

    async with sessions() as session:
        row = await session.scalar(select(ProjectRow))
        analysis_count = await session.scalar(select(func.count(AnalysisRow.id)))
        draft_count = await session.scalar(select(func.count(ProposalDraftRow.id)))
        submission_count = await session.scalar(select(func.count(SubmissionRow.id)))

    assert row is not None
    assert row.state == ProjectState.DRAFTED.value
    assert row.karlancer_project_id == "322263"
    assert Decimal(row.extracted_data["budget_max"]) == Decimal("3000000")
    assert analysis_count == 1
    assert draft_count == 1
    assert submission_count == 0
