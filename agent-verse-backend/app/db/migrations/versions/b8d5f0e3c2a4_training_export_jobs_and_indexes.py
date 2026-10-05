"""training_export_jobs + indexes for the streamed training export (OPS-37)

* ``training_export_jobs``: durable per-tenant export jobs (FORCE RLS) run by a
  Celery worker that writes the JSONL to object storage; claims are fenced by
  ``claim_token`` and kept alive by ``heartbeat_at``.
* ``ix_goals_tenant_completed_created``: partial index for the export's keyset
  walk over a tenant's completed goals (newest first).
* ``ix_evaluations_goal_created`` / ``ix_eval_scorecards_goal_created``: the
  per-goal "latest score" lookup (``ORDER BY created_at DESC LIMIT 1``) instead
  of DISTINCT ON over the tenant's whole evaluation ledger.

The indexes are built CONCURRENTLY (no write lock on goals/evaluations).

Revision ID: b8d5f0e3c2a4
Revises: e2e2811317df
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d5f0e3c2a4"
down_revision: str | None = "e2e2811317df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "training_export_jobs"
_INDEXES = (
    (
        "ix_goals_tenant_completed_created",
        "ON goals (tenant_id, created_at DESC, id DESC) "
        "WHERE status IN ('complete', 'completed')",
    ),
    ("ix_evaluations_goal_created", "ON evaluations (goal_id, created_at DESC)"),
    ("ix_eval_scorecards_goal_created", "ON eval_scorecards (goal_id, created_at DESC)"),
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output_format", sa.String(16), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=False),
        sa.Column("row_limit", sa.Integer(), nullable=False),
        sa.Column("example_count", sa.Integer(), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        # Worker liveness + fencing: a claim sets claim_token; heartbeat,
        # completion and failure writes require it, so a stalled worker whose
        # job was re-claimed cannot overwrite the new owner's row.
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(32), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_training_export_jobs_tenant_created",
        _TABLE,
        ["tenant_id", sa.text("created_at DESC"), "id"],
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )
    with op.get_context().autocommit_block():
        for name, spec in _INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _spec in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.drop_table(_TABLE)
