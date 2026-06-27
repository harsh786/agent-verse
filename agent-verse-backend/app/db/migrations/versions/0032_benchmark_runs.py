"""Add benchmark_runs table for DB-backed benchmark storage.

Revision ID: 0032
Revises: 0031
"""
from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id          TEXT        PRIMARY KEY,
            tenant_id   TEXT        NOT NULL DEFAULT 'global',
            suite_name  TEXT        NOT NULL,
            score       FLOAT       NOT NULL DEFAULT 0.0,
            metadata    JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_benchmark_runs_suite ON benchmark_runs (suite_name, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_benchmark_runs_suite")
    op.execute("DROP TABLE IF EXISTS benchmark_runs")
