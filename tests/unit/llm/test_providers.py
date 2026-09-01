from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from karlancer_agent.classification.rules import RuleAssessment, RuleDecision
from karlancer_agent.domain.enums import AnalysisDecision
from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.llm.base import ProviderUnavailableError, is_auto_eligible
from karlancer_agent.llm.ollama import OllamaProvider
from karlancer_agent.llm.openai_compatible import OpenAICompatibleProvider


def project() -> ExtractedProject:
    return ExtractedProject(
        project_id="322263",
        canonical_url="https://www.karlancer.com/projects/322263/example",
        title="ساخت ربات تلگرام با پایتون",
        description="یک ربات تلگرام با وب‌هوک و پایگاه داده نیاز دارم.",
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


def rules() -> RuleAssessment:
    return RuleAssessment(
        positive_categories=frozenset({"python", "messaging_bots"}),
        matched_terms=frozenset({"python", "telegram bot"}),
        excluded_terms=frozenset(),
        score=12,
        decision=RuleDecision.ACCEPT,
        glossary_version="1.0.0",
    )


ANALYSIS = {
    "category": "messaging_bot",
    "decision": "eligible",
    "confidence": 0.92,
    "reasons": ["خروجی اصلی یک ربات برنامه‌نویسی‌شده است"],
    "technical_approach": "پیاده‌سازی webhook، persistence و تست مسیر پیام",
    "programming_deliverable": "ربات تلگرام پایتون",
    "matched_domains": ["python", "messaging_bots"],
    "hard_negative": None,
    "ambiguities": [],
    "recommended_questions": ["ربات روی کدام سرور deploy می‌شود؟"],
}

DRAFT = {
    "project_understanding": "ربات تلگرام پایدار با وب‌هوک و ذخیره پیام‌ها نیاز دارید.",  # noqa: RUF001
    "technical_approach": "ساخت backend پایتون، اتصال webhook و تست سناریوهای اصلی.",
    "questions": ["سرور مقصد از قبل آماده است؟"],
}


def response_for(provider: str, payload: dict[str, object]) -> dict[str, object]:
    import json

    content = json.dumps(payload, ensure_ascii=False)
    if provider == "openai":
        return {"choices": [{"message": {"content": content}}]}
    return {"message": {"content": content}, "done": True}


ProviderFactory = Callable[[httpx.AsyncClient], object]


@pytest.fixture(params=("openai", "ollama"))
def provider_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def make_provider(kind: str, client: httpx.AsyncClient):
    if kind == "openai":
        return OpenAICompatibleProvider(
            base_url="https://llm.example/v1",
            api_key="secret",
            model="compatible-model",
            client=client,
        )
    return OllamaProvider(
        base_url="http://ollama.test:11434",
        model="local-model",
        client=client,
    )


@pytest.mark.asyncio
async def test_provider_contract_analyzes_to_domain_result(provider_kind: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        assert body["stream"] is False
        if provider_kind == "openai":
            assert request.url.path == "/v1/chat/completions"
            assert body["response_format"]["type"] == "json_schema"
            assert request.headers["Authorization"] == "Bearer secret"
        else:
            assert request.url.path == "/api/chat"
            assert body["format"]["type"] == "object"
        return httpx.Response(200, json=response_for(provider_kind, ANALYSIS))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(provider_kind, client)
        result = await provider.analyze(project(), rules())

    assert result.decision is AnalysisDecision.ELIGIBLE
    assert result.confidence == 0.92
    assert result.category == "messaging_bot"
    assert result.technical_approach.startswith("پیاده‌سازی")
    assert len(result.recommended_questions) == 1


@pytest.mark.asyncio
async def test_provider_contract_drafts_only_structured_middle(provider_kind: str) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            body = __import__("json").loads(request.content)
            assert "45-70 Persian words" in body["messages"][0]["content"]
        payload = ANALYSIS if calls == 1 else DRAFT
        return httpx.Response(200, json=response_for(provider_kind, payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(provider_kind, client)
        analysis = await provider.analyze(project(), rules())
        core = await provider.draft(project(), analysis)

    assert core.project_understanding.startswith("ربات تلگرام")
    assert len(core.questions) <= 2
    assert "سلام" not in core.project_understanding


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("invalid_json", "incomplete", "timeout"))
async def test_analysis_retries_once_then_falls_back_to_needs_review(
    provider_kind: str, failure: str
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "invalid_json":
            content = "not-json"
            payload = (
                {"choices": [{"message": {"content": content}}]}
                if provider_kind == "openai"
                else {"message": {"content": content}, "done": True}
            )
            return httpx.Response(200, json=payload)
        invalid = dict(ANALYSIS)
        invalid.pop("confidence")
        return httpx.Response(200, json=response_for(provider_kind, invalid))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await make_provider(provider_kind, client).analyze(project(), rules())

    assert calls == 2
    assert result.decision is AnalysisDecision.NEEDS_REVIEW
    assert result.confidence == 0
    assert "provider" in result.reason.lower()


@pytest.mark.asyncio
async def test_parse_failure_is_retried_once_and_can_recover(provider_kind: str) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload: dict[str, object]
        if calls == 1:
            payload = (
                {"choices": [{"message": {"content": "not-json"}}]}
                if provider_kind == "openai"
                else {"message": {"content": "not-json"}, "done": True}
            )
        else:
            payload = response_for(provider_kind, ANALYSIS)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await make_provider(provider_kind, client).analyze(project(), rules())

    assert calls == 2
    assert result.decision is AnalysisDecision.ELIGIBLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    (
        {"confidence": 0.84},
        {"hard_negative": "tool-only video editing"},
    ),
)
async def test_provider_eligible_output_is_downgraded_when_gate_fails(
    provider_kind: str, override: dict[str, object]
) -> None:
    unsafe = {**ANALYSIS, **override}

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_for(provider_kind, unsafe))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await make_provider(provider_kind, client).analyze(project(), rules())

    assert result.decision is AnalysisDecision.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_draft_retries_once_then_raises_typed_error(provider_kind: str) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response_for(provider_kind, {"questions": []}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = make_provider(provider_kind, client)
        with pytest.raises(ProviderUnavailableError):
            await provider.draft(
                project(),
                project_analysis(AnalysisDecision.ELIGIBLE, confidence=0.9),
            )

    assert calls == 2


@pytest.mark.asyncio
async def test_http_400_is_not_retried(provider_kind: str) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad request"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await make_provider(provider_kind, client).analyze(project(), rules())

    assert calls == 1
    assert result.decision is AnalysisDecision.NEEDS_REVIEW


def project_analysis(
    decision: AnalysisDecision,
    *,
    confidence: float,
    hard_negative: str | None = None,
):
    from karlancer_agent.domain.models import AnalysisResult

    return AnalysisResult(
        decision=decision,
        confidence=confidence,
        programming_deliverable="Python service",
        matched_domains=("python",),
        hard_negative=hard_negative,
        reason="test",
        technical_approach="test approach",
    )


@pytest.mark.parametrize(
    ("analysis", "extractor_valid", "proposal_valid", "expected"),
    (
        (project_analysis(AnalysisDecision.ELIGIBLE, confidence=0.85), True, True, True),
        (project_analysis(AnalysisDecision.ELIGIBLE, confidence=0.849), True, True, False),
        (project_analysis(AnalysisDecision.NEEDS_REVIEW, confidence=0.99), True, True, False),
        (
            project_analysis(
                AnalysisDecision.ELIGIBLE,
                confidence=0.99,
                hard_negative="tool-only video editing",
            ),
            True,
            True,
            False,
        ),
        (project_analysis(AnalysisDecision.ELIGIBLE, confidence=0.99), False, True, False),
        (project_analysis(AnalysisDecision.ELIGIBLE, confidence=0.99), True, False, False),
    ),
)
def test_auto_eligibility_gate(analysis, extractor_valid, proposal_valid, expected) -> None:
    assert is_auto_eligible(
        analysis,
        extractor_valid=extractor_valid,
        proposal_valid=proposal_valid,
    ) is expected
