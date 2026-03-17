"""002 — Audit log protection trigger, casbin_rule table, system_config table.

Revision ID: 002_audit_log_protect
Revises: 0c589a44ca97
Create Date: 2026-03-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic
revision: str = "002_audit_log_protect"
down_revision: str = "0c589a44ca97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. Audit log protection — prevent UPDATE and DELETE on audit_logs
    # -----------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit log records cannot be modified or deleted';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_modification();
    """)

    op.execute("""
        CREATE TRIGGER audit_log_no_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_modification();
    """)

    # -----------------------------------------------------------------------
    # 2. Casbin rule table for RBAC policies
    # -----------------------------------------------------------------------
    op.create_table(
        "casbin_rule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ptype", sa.String(length=255), nullable=False, server_default="p"),
        sa.Column("v0", sa.String(length=255), server_default=""),
        sa.Column("v1", sa.String(length=255), server_default=""),
        sa.Column("v2", sa.String(length=255), server_default=""),
        sa.Column("v3", sa.String(length=255), server_default=""),
        sa.Column("v4", sa.String(length=255), server_default=""),
        sa.Column("v5", sa.String(length=255), server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )

    # -----------------------------------------------------------------------
    # 3. System configuration table (encrypted values)
    # -----------------------------------------------------------------------
    op.create_table(
        "system_config",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_config")
    op.drop_table("casbin_rule")

    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_logs;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_modification();")
