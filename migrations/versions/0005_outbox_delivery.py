"""Bounded durable delivery attempts and Telegram receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0005_outbox_delivery"
down_revision = "0004_provider_circuit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "outbox_events",
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("outbox_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("outbox_events", sa.Column("telegram_message_id", sa.BigInteger()))


def downgrade():
    op.drop_column("outbox_events", "telegram_message_id")
    op.drop_column("outbox_events", "next_attempt_at")
    op.drop_column("outbox_events", "delivery_attempts")
