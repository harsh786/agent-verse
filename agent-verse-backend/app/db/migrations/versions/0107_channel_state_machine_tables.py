"""0107 — channel_tenant_mappings, state_machines, and state_machine_instances tables.

Revision ID: 0107
Revises: 0106
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ── Alembic metadata ──────────────────────────────────────────────────────────
revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── channel_tenant_mappings ───────────────────────────────────────────────
    op.create_table(
        "channel_tenant_mappings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("channel_config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_channel_tenant_mappings_tenant_id", "channel_tenant_mappings", ["tenant_id"]
    )
    op.create_index(
        "ix_channel_tenant_unique",
        "channel_tenant_mappings",
        ["tenant_id", "channel_type"],
        unique=True,
    )

    # RLS
    op.execute("ALTER TABLE channel_tenant_mappings ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY channel_tenant_isolation ON channel_tenant_mappings
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

    # ── state_machines ────────────────────────────────────────────────────────
    op.create_table(
        "state_machines",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("states", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("transitions", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("initial_state", sa.String(128), nullable=False, server_default="start"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_state_machines_tenant_id", "state_machines", ["tenant_id"])

    op.execute("ALTER TABLE state_machines ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY state_machine_isolation ON state_machines
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

    # ── state_machine_instances ───────────────────────────────────────────────
    op.create_table(
        "state_machine_instances",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("machine_id", sa.String(64), nullable=False),
        sa.Column("current_state", sa.String(128), nullable=False),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("history", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["machine_id"], ["state_machines.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_smi_tenant_id", "state_machine_instances", ["tenant_id"])
    op.create_index("ix_smi_machine_id", "state_machine_instances", ["machine_id"])

    op.execute("ALTER TABLE state_machine_instances ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY smi_isolation ON state_machine_instances
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)


def downgrade() -> None:
    op.drop_table("state_machine_instances")
    op.drop_table("state_machines")
    op.drop_table("channel_tenant_mappings")
