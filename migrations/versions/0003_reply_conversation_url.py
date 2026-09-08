"""Persist the Karlancer conversation URL for reply monitoring.

Revision ID: 0003_reply_conversation_url
Revises: 0002_assisted_approval
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_reply_conversation_url"
down_revision: str | None = "0002_assisted_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("conversation_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "conversation_url")
