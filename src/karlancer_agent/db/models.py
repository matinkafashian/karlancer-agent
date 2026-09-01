"""Durable database records and uniqueness constraints."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from karlancer_agent.db.base import Base
from karlancer_agent.domain.enums import ProjectState


def utc_now() -> datetime:
    """Return an aware UTC timestamp for Python-side defaults."""

    return datetime.now(UTC)


class ProjectRow(Base):
    """Canonical project record shared by every workflow stage."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("channel_message_id", name="uq_projects_channel_message_id"),
        UniqueConstraint("canonical_project_url", name="uq_projects_canonical_url"),
        UniqueConstraint("karlancer_project_id", name="uq_projects_karlancer_id"),
        CheckConstraint(
            "state IN ('discovered', 'extract_pending', 'extracted', 'rejected', "
            "'needs_review', 'eligible', 'drafted', 'awaiting_approval', "
            "'submit_pending', 'submitted', 'reply_watch', 'reply_notified', "
            "'human_handoff', 'retryable_error', 'paused_error')",
            name="ck_projects_valid_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    channel_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    karlancer_project_id: Mapped[str | None] = mapped_column(String(128))
    canonical_project_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_channel_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProjectState.DISCOVERED.value, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AnalysisRow(Base):
    """Versioned rules and model decision for a project."""

    __tablename__ = "analyses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_score: Mapped[int] = mapped_column(nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    reasons: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ProposalDraftRow(Base):
    """Immutable proposal draft versions."""

    __tablename__ = "proposal_drafts"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_draft_project_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    validator_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SubmissionRow(Base):
    """Single reservation/receipt per project, preventing duplicate submits."""

    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_submissions_project_id"),
        UniqueConstraint("idempotency_key", name="uq_submissions_idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    form_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    duration_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    duration_unit: Mapped[str | None] = mapped_column(String(32))
    evidence_url: Mapped[str | None] = mapped_column(Text)
    evidence_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReplyEventRow(Base):
    """The only employer reply event permitted for a project."""

    __tablename__ = "reply_events"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_reply_events_project_id"),
        UniqueConstraint(
            "project_id", "employer_message_id", name="uq_reply_project_message"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    employer_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    conversation_url: Mapped[str] = mapped_column(Text, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AuditEventRow(Base):
    """Append-only operational history without secrets."""

    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SourceCursorRow(Base):
    """Last safely committed source message for overlap polling."""

    __tablename__ = "source_cursors"

    source: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class OutboxEventRow(Base):
    """Recoverable notification waiting to be delivered externally."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_outbox_deduplication_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
