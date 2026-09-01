from decimal import Decimal

import pytest
from pydantic import ValidationError

from karlancer_agent.domain.enums import AnalysisDecision, ProjectState
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.domain.state_machine import InvalidStateTransition, transition

EXPECTED_STATES = {
    "discovered",
    "extract_pending",
    "extracted",
    "rejected",
    "needs_review",
    "eligible",
    "drafted",
    "awaiting_approval",
    "submit_pending",
    "submitted",
    "reply_watch",
    "reply_notified",
    "human_handoff",
    "retryable_error",
    "paused_error",
}


def test_state_values_match_approved_design() -> None:
    assert {state.value for state in ProjectState} == EXPECTED_STATES


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ProjectState.DISCOVERED, ProjectState.EXTRACT_PENDING),
        (ProjectState.EXTRACT_PENDING, ProjectState.EXTRACTED),
        (ProjectState.EXTRACTED, ProjectState.REJECTED),
        (ProjectState.EXTRACTED, ProjectState.NEEDS_REVIEW),
        (ProjectState.EXTRACTED, ProjectState.ELIGIBLE),
        (ProjectState.NEEDS_REVIEW, ProjectState.ELIGIBLE),
        (ProjectState.NEEDS_REVIEW, ProjectState.REJECTED),
        (ProjectState.ELIGIBLE, ProjectState.DRAFTED),
        (ProjectState.DRAFTED, ProjectState.AWAITING_APPROVAL),
        (ProjectState.DRAFTED, ProjectState.SUBMIT_PENDING),
        (ProjectState.AWAITING_APPROVAL, ProjectState.DRAFTED),
        (ProjectState.AWAITING_APPROVAL, ProjectState.REJECTED),
        (ProjectState.AWAITING_APPROVAL, ProjectState.SUBMIT_PENDING),
        (ProjectState.SUBMIT_PENDING, ProjectState.SUBMITTED),
        (ProjectState.SUBMITTED, ProjectState.REPLY_WATCH),
        (ProjectState.REPLY_WATCH, ProjectState.REPLY_NOTIFIED),
        (ProjectState.REPLY_NOTIFIED, ProjectState.HUMAN_HANDOFF),
    ],
)
def test_valid_transition_returns_target(
    current: ProjectState, target: ProjectState
) -> None:
    assert transition(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ProjectState.DISCOVERED, ProjectState.SUBMITTED),
        (ProjectState.EXTRACTED, ProjectState.DRAFTED),
        (ProjectState.DRAFTED, ProjectState.SUBMITTED),
        (ProjectState.REJECTED, ProjectState.ELIGIBLE),
        (ProjectState.HUMAN_HANDOFF, ProjectState.REPLY_WATCH),
        (ProjectState.HUMAN_HANDOFF, ProjectState.SUBMIT_PENDING),
        (ProjectState.SUBMITTED, ProjectState.SUBMITTED),
    ],
)
def test_invalid_transition_raises(current: ProjectState, target: ProjectState) -> None:
    with pytest.raises(InvalidStateTransition, match=f"{current.value} -> {target.value}"):
        transition(current, target)


@pytest.mark.parametrize("terminal", [ProjectState.REJECTED, ProjectState.HUMAN_HANDOFF])
def test_terminal_states_have_no_outgoing_transition(terminal: ProjectState) -> None:
    for target in ProjectState:
        with pytest.raises(InvalidStateTransition):
            transition(terminal, target)


def test_extracted_project_rejects_inverted_budget() -> None:
    with pytest.raises(ValidationError, match="budget_max"):
        ExtractedProject(
            project_id="project-123",
            canonical_url="https://www.karlancer.com/projects/123/example",
            title="پیاده‌سازی RAG",
            description="ساخت سامانه پرسش و پاسخ با پایتون",
            skills=("Python", "RAG"),
            budget_text="10000000 to 20000000 IRR",
            budget_min=Decimal("20000000"),
            budget_max=Decimal("10000000"),
            currency="IRR",
            duration_text="7 days",
            duration_value=Decimal("7"),
            duration_unit="day",
            is_open=True,
            proposal_form_url="https://www.karlancer.com/projects/123/proposal",
        )


def test_analysis_result_allows_at_most_two_questions() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            decision=AnalysisDecision.NEEDS_REVIEW,
            confidence=0.7,
            programming_deliverable="Python API",
            matched_domains=("python",),
            hard_negative=None,
            ambiguities=("scope",),
            recommended_questions=("Q1?", "Q2?", "Q3?"),
            reason="Scope needs clarification.",
        )


def test_domain_models_are_immutable() -> None:
    analysis = AnalysisResult(
        decision=AnalysisDecision.ELIGIBLE,
        confidence=0.95,
        programming_deliverable="RAG service",
        matched_domains=("rag", "python"),
        hard_negative=None,
        ambiguities=(),
        recommended_questions=(),
        reason="Clear programming deliverable.",
    )

    with pytest.raises(ValidationError):
        analysis.confidence = 0.1
