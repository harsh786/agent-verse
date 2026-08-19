"""Persist immutable Magentic ledger versions and Mixture-of-Agents artifacts.

Revision ID: 0101_magentic_moa
Revises: 0100_handoffs_group_chat
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def _base_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_progress_ledger_tenant_session_version",
        "progress_ledger_revisions",
        ["tenant_id", "session_id", "version"],
    )
    op.create_index(
        "ix_progress_ledger_tenant_session_version",
        "progress_ledger_revisions",
        ["tenant_id", "session_id", "version"],
    )
    op.create_table(
        "moa_layers",
        *_base_columns(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("strategy_execution_id", sa.Text(), nullable=False),
        sa.Column("layer_index", sa.Integer(), nullable=False),
        sa.Column("aggregator_deployment_id", sa.Text(), nullable=False),
        sa.Column("deployment_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("quorum", sa.Integer(), nullable=False),
        sa.Column("aggregate_reference", sa.Text(), nullable=True),
        sa.Column("explanation", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.CheckConstraint("layer_index >= 0", name="ck_moa_layers_index_nonnegative"),
        sa.CheckConstraint("quorum > 0", name="ck_moa_layers_quorum_positive"),
        sa.UniqueConstraint(
            "tenant_id",
            "strategy_execution_id",
            "layer_index",
            name="uq_moa_layers_execution_index",
        ),
    )
    op.create_index(
        "ix_moa_layers_tenant_session_index",
        "moa_layers",
        ["tenant_id", "session_id", "layer_index"],
    )
    op.create_table(
        "moa_proposals",
        *_base_columns(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("strategy_execution_id", sa.Text(), nullable=False),
        sa.Column("layer_index", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model_family", sa.Text(), nullable=False),
        sa.Column("deployment_id", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("failure_domain", sa.Text(), nullable=False),
        sa.Column(
            "prompt_input_references",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("proposal_reference", sa.Text(), nullable=False),
        sa.Column("safe_excerpt", sa.Text(), nullable=False),
        sa.Column("evidence_references", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "predecessor_proposal_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.CheckConstraint("layer_index >= 0", name="ck_moa_proposals_index_nonnegative"),
        sa.CheckConstraint("attempt > 0", name="ck_moa_proposals_attempt_positive"),
        sa.CheckConstraint("tokens >= 0", name="ck_moa_proposals_tokens_nonnegative"),
        sa.UniqueConstraint(
            "tenant_id",
            "strategy_execution_id",
            "layer_index",
            "participant_id",
            "attempt",
            name="uq_moa_proposals_attempt",
        ),
    )
    op.create_index(
        "ix_moa_proposals_tenant_execution_layer",
        "moa_proposals",
        ["tenant_id", "strategy_execution_id", "layer_index"],
    )
    _rls("moa_layers")
    _rls("moa_proposals")


def downgrade() -> None:
    op.drop_table("moa_proposals")
    op.drop_table("moa_layers")
    op.drop_index(
        "ix_progress_ledger_tenant_session_version",
        table_name="progress_ledger_revisions",
    )
    op.drop_constraint(
        "uq_progress_ledger_tenant_session_version",
        "progress_ledger_revisions",
        type_="unique",
    )
