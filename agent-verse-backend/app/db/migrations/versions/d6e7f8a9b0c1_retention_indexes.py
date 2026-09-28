"""Indexes for batched retention on the append-only / high-volume tables.

The retention job (``app.scaling.tasks._delete_expired_records``) and workflow
run retention (``WorkflowRunStore.delete_expired_runs``) now delete in bounded
batches selected by an age column. Without an index on that column every batch
is a sequential scan of the whole table.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None

_INDEXES = (
    ("trigger_events", "ix_trigger_events_fired_at", "(fired_at)", ""),
    ("decision_traces", "ix_decision_traces_created_at", "(created_at)", ""),
    (
        "memory_records",
        "ix_memory_records_expires_at",
        "(expires_at)",
        "WHERE expires_at IS NOT NULL",
    ),
    (
        "workflow_runs",
        "ix_workflow_runs_terminal_created",
        "(created_at)",
        "WHERE status IN ('complete', 'failed', 'cancelled', 'timed_out')",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        r[0]
        for r in bind.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).fetchall()
    }
    for table, name, cols, where in _INDEXES:
        if table in existing:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols} {where}".strip())


def downgrade() -> None:
    for _table, name, _cols, _where in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
