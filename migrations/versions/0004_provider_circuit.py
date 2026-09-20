"""Persist model-provider cooldown state.

Revision ID: 0004_provider_circuit
Revises: 0003_reply_conversation_url
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_provider_circuit"
down_revision: str | None = "0003_reply_conversation_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_circuits",
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="closed",
        ),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('closed', 'cooldown', 'hard_paused')",
            name="ck_provider_circuits_valid_status",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_provider_circuits_nonnegative_failures",
        ),
        sa.PrimaryKeyConstraint("provider"),
    )


def downgrade() -> None:
    op.drop_table("provider_circuits")
