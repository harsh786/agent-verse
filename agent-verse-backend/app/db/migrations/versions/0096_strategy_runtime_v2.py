"""Add versioned strategy runtime profiles and certification evidence.

Revision ID: 0096_strategy_runtime_v2
Revises: 0095_raft_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0096_strategy_runtime_v2"
down_revision = "0095_raft_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("goals", sa.Column("runtime_profile_version", sa.Integer()))
    op.add_column("goals", sa.Column("strategy_registry_revision", sa.String(80)))
    op.add_column("goals", sa.Column("runtime_profile_snapshot", postgresql.JSONB()))
    op.add_column(
        "goals",
        sa.Column(
            "rejected_strategies",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "ix_goals_tenant_runtime_profile",
        "goals",
        ["tenant_id", "runtime_profile_version"],
    )

    op.create_table(
        "strategy_certification_evidence",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("strategy_id", sa.String(100), nullable=False),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("state_schema_version", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("artifact_reference", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("expires_at > observed_at", name="ck_strategy_evidence_expiry"),
    )
    op.create_index(
        "ix_strategy_evidence_tenant_strategy",
        "strategy_certification_evidence",
        ["tenant_id", "strategy_id", "adapter_version"],
    )
    op.execute("ALTER TABLE strategy_certification_evidence ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE strategy_certification_evidence FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY strategy_certification_evidence_tenant_isolation
        ON strategy_certification_evidence
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS strategy_certification_evidence_tenant_isolation "
        "ON strategy_certification_evidence"
    )
    op.drop_index(
        "ix_strategy_evidence_tenant_strategy",
        table_name="strategy_certification_evidence",
    )
    op.drop_table("strategy_certification_evidence")
    op.drop_index("ix_goals_tenant_runtime_profile", table_name="goals")
    op.drop_column("goals", "rejected_strategies")
    op.drop_column("goals", "runtime_profile_snapshot")
    op.drop_column("goals", "strategy_registry_revision")
    op.drop_column("goals", "runtime_profile_version")
