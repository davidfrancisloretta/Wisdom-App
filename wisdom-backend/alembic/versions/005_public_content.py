"""005 — Add public content, workshops, workshop registrations, and counselor profiles tables.

Revision ID: 005_public_content
Revises: 004_scheduled_notifications
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005_public_content"
down_revision = "004_scheduled_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- public_content ---
    op.create_table(
        "public_content",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_type",
            sa.String(50),
            nullable=False,
            comment="article/resource/workshop/forum_post/crisis_info",
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_public_content_slug", "public_content", ["slug"])
    op.create_index("ix_public_content_content_type", "public_content", ["content_type"])
    op.create_index("ix_public_content_is_published", "public_content", ["is_published"])

    # --- workshops ---
    op.create_table(
        "workshops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("facilitator_name", sa.String(255), nullable=True),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "location",
            sa.String(50),
            nullable=True,
            comment="online/in_person/hybrid",
        ),
        sa.Column("meeting_link", sa.String(500), nullable=True),
        sa.Column("capacity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("registered_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("registration_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workshops_start_datetime", "workshops", ["start_datetime"])

    # --- workshop_registrations ---
    op.create_table(
        "workshop_registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workshop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workshops.id"), nullable=False),
        sa.Column("registrant_name", sa.String(255), nullable=False),
        sa.Column("registrant_email", sa.String(255), nullable=True),
        sa.Column("registrant_phone", sa.String(20), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("attended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # --- counselor_profiles ---
    op.create_table(
        "counselor_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("specializations", postgresql.JSONB(), nullable=True),
        sa.Column("languages", postgresql.JSONB(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("is_accepting_referrals", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_counselor_profiles_is_accepting_referrals", "counselor_profiles", ["is_accepting_referrals"])


def downgrade() -> None:
    op.drop_index("ix_counselor_profiles_is_accepting_referrals")
    op.drop_table("counselor_profiles")
    op.drop_table("workshop_registrations")
    op.drop_index("ix_workshops_start_datetime")
    op.drop_table("workshops")
    op.drop_index("ix_public_content_is_published")
    op.drop_index("ix_public_content_content_type")
    op.drop_index("ix_public_content_slug")
    op.drop_table("public_content")
