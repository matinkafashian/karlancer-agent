import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from karlancer_agent.classification.rules import RuleAssessment, RuleDecision
from karlancer_agent.domain.enums import AnalysisDecision
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.llm.base import (
    ProviderAuthenticationError,
    ProviderQuotaError,
)
from karlancer_agent.llm.codex_cli import (
    CodexCLIProvider,
    CodexProcessResult,
    build_codex_command,
)


def _project() -> ExtractedProject:
    return ExtractedProject(
        project_id="322263",
        canonical_url="https://www.karlancer.com/projects/322263/example",
        title="ساخت ربات تلگرام با پایتون",
        description="ربات تلگرام با وب‌هوک و پایگاه داده نیاز دارم.",
        skills=("Python", "Telegram Bot"),
        budget_text="۱ تا ۳ میلیون تومان",  # noqa: RUF001
        budget_min=Decimal("1000000"),
        budget_max=Decimal("3000000"),
        currency="تومان",
        duration_text="۱ روز",  # noqa: RUF001
        duration_value=Decimal("1"),
        duration_unit="روز",
        is_open=True,
        proposal_form_url="https://www.karlancer.com/projects/322263/example#proposal",
    )


def _rules() -> RuleAssessment:
    return RuleAssessment(
        positive_categories=frozenset({"python", "messaging_bots"}),
        matched_terms=frozenset({"python", "telegram bot"}),
        excluded_terms=frozenset(),
        score=12,
        decision=RuleDecision.ACCEPT,
        glossary_version="1.0.0",
    )


VALID_ANALYSIS = {
    "category": "messaging_bot",
    "decision": "eligible",
    "confidence": 0.92,
    "reasons": ["خروجی اصلی یک ربات برنامه‌نویسی‌شده است"],
    "technical_approach": "پیاده‌سازی webhook و persistence",
    "programming_deliverable": "ربات تلگرام پایتون",
    "matched_domains": ["python", "messaging_bots"],
    "hard_negative": None,
    "ambiguities": [],
    "recommended_questions": [],
}

VALID_DRAFT = {
    "project_understanding": "ربات تلگرام پایدار با ذخیره پیام‌ها نیاز دارید.",  # noqa: RUF001
    "technical_approach": "بک‌اند پایتون، وب‌هوک و تست مسیرهای اصلی را پیاده می‌کنم.",
    "questions": [],
}


class FakeRunner:
    def __init__(self, results: list[CodexProcessResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> CodexProcessResult:
        self.calls.append(kwargs)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_codex_provider_requests_schema_constrained_ephemeral_run() -> None:
    runner = FakeRunner(
        [CodexProcessResult(returncode=0, output=json.dumps(VALID_ANALYSIS), stderr="")]
    )
    provider = CodexCLIProvider(
        model="gpt-5.6-luna",
        executable="codex",
        reasoning_effort="low",
        timeout_seconds=120,
        runner=runner,
    )

    result = await provider.analyze(_project(), _rules())

    assert result.decision is AnalysisDecision.ELIGIBLE
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["model"] == "gpt-5.6-luna"
    assert call["reasoning_effort"] == "low"
    assert call["timeout_seconds"] == 120
    assert call["schema"]["additionalProperties"] is False  # type: ignore[index]
    assert "Do not use tools" in str(call["prompt"])


def test_codex_command_is_ephemeral_read_only_and_reads_prompt_from_stdin() -> None:
    command = build_codex_command(
        executable="codex",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        schema_path="/tmp/schema.json",
        output_path="/tmp/output.json",
        working_directory="/tmp/empty",
    )

    assert command == (
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--cd",
        "/tmp/empty",
        "--model",
        "gpt-5.6-luna",
        "-c",
        'model_reasoning_effort="low"',
        "--output-schema",
        "/tmp/schema.json",
        "--output-last-message",
        "/tmp/output.json",
        "-",
    )


@pytest.mark.asyncio
async def test_quota_error_is_not_retried() -> None:
    runner = FakeRunner(
        [
            CodexProcessResult(
                returncode=1,
                output="",
                stderr="You've hit your usage limit. Try again in 5 hours.",
            )
        ]
    )
    provider = CodexCLIProvider(model="gpt-5.6-luna", runner=runner)

    with pytest.raises(ProviderQuotaError) as caught:
        await provider.draft(
            _project(),
            AnalysisResult(
                decision=AnalysisDecision.ELIGIBLE,
                confidence=0.92,
                programming_deliverable="ربات تلگرام",
                hard_negative=None,
                reason="مرتبط",
            ),
        )

    assert len(runner.calls) == 1
    assert caught.value.retry_at is not None
    remaining = caught.value.retry_at - datetime.now(UTC)
    assert timedelta(hours=4, minutes=59) < remaining <= timedelta(hours=5)


@pytest.mark.asyncio
async def test_authentication_error_is_not_retried() -> None:
    runner = FakeRunner(
        [
            CodexProcessResult(
                returncode=1,
                output="",
                stderr="Not logged in. Run codex login.",
            )
        ]
    )
    provider = CodexCLIProvider(model="gpt-5.6-luna", runner=runner)

    with pytest.raises(ProviderAuthenticationError):
        await provider.analyze(_project(), _rules())

    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_invalid_structured_output_keeps_the_existing_single_retry() -> None:
    runner = FakeRunner(
        [
            CodexProcessResult(returncode=0, output="not-json", stderr=""),
            CodexProcessResult(returncode=0, output=json.dumps(VALID_ANALYSIS), stderr=""),
        ]
    )
    provider = CodexCLIProvider(model="gpt-5.6-luna", runner=runner)

    result = await provider.analyze(_project(), _rules())

    assert result.decision is AnalysisDecision.ELIGIBLE
    assert len(runner.calls) == 2
