"""004 — Add scheduled_notifications table.

Revision ID: 004_scheduled_notifications
Revises: 003_notifications
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_scheduled_notifications"
down_revision = "003_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("notification_type", sa.String(100), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scheduled_notifications_status", "scheduled_notifications", ["status"])
    op.create_index("ix_scheduled_notifications_scheduled_at", "scheduled_notifications", ["scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_notifications_scheduled_at")
    op.drop_index("ix_scheduled_notifications_status")
    op.drop_table("scheduled_notifications")
