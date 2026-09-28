"""goal_cost_breakdowns + simulation_runs: persist what lived in per-process memory.

* Per-goal planner/executor/verifier cost attribution was kept in a
  module-level dict (``app/observability/cost_breakdown.py``), mirrored into
  Redis only by the API lifespan. A goal executed by a Celery worker or another
  API replica recorded into that process's memory, so
  ``GET /goals/{id}/cost-metrics`` elsewhere was empty and a restart lost it.
  Rows are now upserted additively (atomic under concurrent role calls).
* Enterprise simulation runs were kept in ``SimulationRunner._runs`` /
  ``_run_tenant`` dicts, so ``GET /enterprise/simulation/{run_id}`` on another
  replica 404'd and a restart lost every run.

Both tables are tenant-isolated by FORCE'd RLS using ``app_current_tenant_uuid()``
(``c9d0e1f2a3b4``), which matches dashless tenant ids and never raises.

Revision ID: f7a8b9c0d1e2
Revises: b4c5d6e7f8a9
"""

from __future__ import annotations

from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

_TABLES = ("goal_cost_breakdowns", "simulation_runs")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_cost_breakdowns (
            tenant_id         UUID NOT NULL,
            goal_id           VARCHAR(255) NOT NULL,
            role              VARCHAR(32) NOT NULL,
            model             VARCHAR(255) NOT NULL DEFAULT '',
            input_tokens      BIGINT NOT NULL DEFAULT 0,
            output_tokens     BIGINT NOT NULL DEFAULT 0,
            cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
            calls             INTEGER NOT NULL DEFAULT 0,
            first_recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, goal_id, role, model)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS simulation_runs (
            tenant_id  UUID NOT NULL,
            run_id     VARCHAR(64) NOT NULL,
            goal       TEXT NOT NULL DEFAULT '',
            status     VARCHAR(32) NOT NULL,
            payload    JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, run_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_simulation_runs_tenant_created "
        "ON simulation_runs (tenant_id, created_at DESC)"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant_uuid()) "
            "WITH CHECK (tenant_id = app_current_tenant_uuid())"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
