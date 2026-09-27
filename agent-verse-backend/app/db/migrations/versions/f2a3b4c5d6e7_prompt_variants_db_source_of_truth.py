"""prompt_variants: aggregate evidence columns + tenant isolation.

``PromptOptimizer`` now reads and writes this table per tenant instead of
hydrating every tenant's variants into each replica at startup:

* evidence is kept as running aggregates — ``run_count``, ``score_sum``,
  ``score_sq_sum`` (mean/variance for the significance test), ``cost_usd_sum``
  / ``cost_samples`` and a fixed-bucket ``latency_hist`` (p95) — so promotion
  can be gated on cost and latency by ``RegressionGate`` without storing an
  unbounded per-run sample list;
* ``promoted_at`` records when a variant became control;
* RLS: a tenant reads its own rows and the shared ``'global'`` rows, and may
  write only its own. Previously the table had no RLS at all.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from __future__ import annotations

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

_HIST_BUCKETS = 14  # len(app.intelligence.prompt_optimizer.LATENCY_BUCKETS_MS)


def upgrade() -> None:
    zeros = "{" + ",".join(["0"] * _HIST_BUCKETS) + "}"
    op.execute(
        "ALTER TABLE prompt_variants "
        "ADD COLUMN IF NOT EXISTS run_count BIGINT NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS score_sum DOUBLE PRECISION NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS score_sq_sum DOUBLE PRECISION NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS cost_usd_sum DOUBLE PRECISION NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS cost_samples BIGINT NOT NULL DEFAULT 0, "
        f"ADD COLUMN IF NOT EXISTS latency_hist INTEGER[] NOT NULL DEFAULT '{zeros}', "
        "ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompt_variants_tenant_key_active "
        "ON prompt_variants (tenant_id, prompt_key) WHERE is_active"
    )
    op.execute("ALTER TABLE prompt_variants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_variants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS prompt_variants_read ON prompt_variants")
    op.execute("DROP POLICY IF EXISTS prompt_variants_own ON prompt_variants")
    # Permissive policies OR together: SELECT sees own + global rows; every
    # write (and its WITH CHECK) is limited to the tenant's own rows.
    op.execute(
        "CREATE POLICY prompt_variants_read ON prompt_variants FOR SELECT "
        "USING (tenant_id = 'global')"
    )
    op.execute(
        "CREATE POLICY prompt_variants_own ON prompt_variants FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS prompt_variants_own ON prompt_variants")
    op.execute("DROP POLICY IF EXISTS prompt_variants_read ON prompt_variants")
    op.execute("ALTER TABLE prompt_variants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_variants DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_prompt_variants_tenant_key_active")
    op.execute(
        "ALTER TABLE prompt_variants DROP COLUMN IF EXISTS promoted_at, "
        "DROP COLUMN IF EXISTS latency_hist, DROP COLUMN IF EXISTS cost_samples, "
        "DROP COLUMN IF EXISTS cost_usd_sum, DROP COLUMN IF EXISTS score_sq_sum, "
        "DROP COLUMN IF EXISTS score_sum, DROP COLUMN IF EXISTS run_count"
    )
