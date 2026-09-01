"""Stable values persisted by the Karlancer agent."""

from enum import StrEnum


class ProjectState(StrEnum):
    """Lifecycle states from discovery through human handoff."""

    DISCOVERED = "discovered"
    EXTRACT_PENDING = "extract_pending"
    EXTRACTED = "extracted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    ELIGIBLE = "eligible"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMIT_PENDING = "submit_pending"
    SUBMITTED = "submitted"
    REPLY_WATCH = "reply_watch"
    REPLY_NOTIFIED = "reply_notified"
    HUMAN_HANDOFF = "human_handoff"
    RETRYABLE_ERROR = "retryable_error"
    PAUSED_ERROR = "paused_error"


class AnalysisDecision(StrEnum):
    """Structured classifier outcomes."""

    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
