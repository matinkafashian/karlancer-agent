"""Create the durable agent schema.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamp_columns() -> list[sa.Column[object]]:
    """Return consistent creation and update columns for this revision."""

    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    projects = op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_message_id", sa.BigInteger(), nullable=False),
        sa.Column("karlancer_project_id", sa.String(length=128), nullable=True),
        sa.Column("canonical_project_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("raw_channel_data", sa.JSON(), nullable=False),
        sa.Column("extracted_data", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint(
            "state IN ('discovered', 'extract_pending', 'extracted', 'rejected', "
            "'needs_review', 'eligible', 'drafted', 'awaiting_approval', "
            "'submit_pending', 'submitted', 'reply_watch', 'reply_notified', "
            "'human_handoff', 'retryable_error', 'paused_error')",
            name="ck_projects_valid_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_project_url", name="uq_projects_canonical_url"),
        sa.UniqueConstraint("channel_message_id", name="uq_projects_channel_message_id"),
        sa.UniqueConstraint("karlancer_project_id", name="uq_projects_karlancer_id"),
    )
    op.create_index("ix_projects_state", "projects", ["state"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("rule_score", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["project_id"], [projects.c.id], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_project_id", "analyses", ["project_id"])

    op.create_table(
        "proposal_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("validator_result", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["project_id"], [projects.c.id], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_draft_project_version"),
    )
    op.create_index("ix_proposal_drafts_project_id", "proposal_drafts", ["project_id"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("form_budget", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("duration_value", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("duration_unit", sa.String(length=32), nullable=True),
        sa.Column("evidence_url", sa.Text(), nullable=True),
        sa.Column("evidence_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], [projects.c.id], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_submissions_idempotency_key"),
        sa.UniqueConstraint("project_id", name="uq_submissions_project_id"),
    )

    op.create_table(
        "reply_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("employer_message_id", sa.String(length=255), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_url", sa.Text(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["project_id"], [projects.c.id], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_reply_events_project_id"),
        sa.UniqueConstraint(
            "project_id", "employer_message_id", name="uq_reply_project_message"
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["project_id"], [projects.c.id], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])

    op.create_table(
        "source_cursors",
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("last_message_id", sa.BigInteger(), nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("source"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deduplication_key", name="uq_outbox_deduplication_key"),
    )
    op.create_index("ix_outbox_events_delivered", "outbox_events", ["delivered"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("source_cursors")
    op.drop_table("audit_events")
    op.drop_table("reply_events")
    op.drop_table("submissions")
    op.drop_table("proposal_drafts")
    op.drop_table("analyses")
    op.drop_table("projects")
