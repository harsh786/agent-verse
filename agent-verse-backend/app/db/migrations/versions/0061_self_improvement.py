"""improvement_experiments, improvement_results, agent_optimization_history

Revision ID: 0061
Revises: 0060
Create Date: 2026-06-28
"""
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Table: improvement_experiments                                       #
    # Tracks A/B experiments for agent prompt optimization                #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS improvement_experiments (
            id                      TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            agent_id                TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            name                    TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'running'
                                    CHECK (status IN
                                        ('running','completed','failed','rolled_back','paused')),
            control_config          JSONB NOT NULL,
            candidate_config        JSONB NOT NULL,
            suggestion_rationale    TEXT NOT NULL DEFAULT '',
            traffic_split_pct       INTEGER NOT NULL DEFAULT 50,
            success_metric          TEXT NOT NULL DEFAULT 'eval_score',
            min_samples_per_arm     INTEGER NOT NULL DEFAULT 20,
            significance_threshold  NUMERIC(4,3) NOT NULL DEFAULT 0.95,
            control_n               INTEGER NOT NULL DEFAULT 0,
            candidate_n             INTEGER NOT NULL DEFAULT 0,
            control_mean            NUMERIC(12,6),
            candidate_mean          NUMERIC(12,6),
            bayesian_uplift         NUMERIC(8,4),
            posterior_prob_better   NUMERIC(4,3),
            winner                  TEXT CHECK (winner IN ('control','candidate','inconclusive')),
            started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at            TIMESTAMPTZ,
            applied_at              TIMESTAMPTZ,
            rolled_back_at          TIMESTAMPTZ,
            rolled_back_reason      TEXT,
            optimizer_version       TEXT NOT NULL DEFAULT '1.0',
            domain                  TEXT,
            created_by              TEXT NOT NULL DEFAULT 'system'
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_experiments_tenant_agent
            ON improvement_experiments(tenant_id, agent_id, started_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_experiments_running
            ON improvement_experiments(tenant_id, status)
            WHERE status = 'running'
    """)

    # ------------------------------------------------------------------ #
    # Table: improvement_results                                          #
    # Per-goal result observations for running experiments                #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS improvement_results (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            experiment_id   TEXT NOT NULL REFERENCES improvement_experiments(id)
                            ON DELETE CASCADE,
            tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            goal_id         TEXT REFERENCES goals(id) ON DELETE SET NULL,
            arm             TEXT NOT NULL CHECK (arm IN ('control','candidate')),
            metric_value    NUMERIC(12,6) NOT NULL,
            metric_name     TEXT NOT NULL,
            goal_completed  BOOLEAN NOT NULL DEFAULT TRUE,
            cost_usd        NUMERIC(10,6),
            latency_ms      INTEGER,
            eval_score      NUMERIC(4,3),
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_improvement_results_experiment
            ON improvement_results(experiment_id, arm, recorded_at DESC)
    """)

    # ------------------------------------------------------------------ #
    # Table: agent_optimization_history                                   #
    # Complete log of all applied optimizations per agent                 #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_optimization_history (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            agent_id        TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            experiment_id   TEXT REFERENCES improvement_experiments(id),
            config_before   JSONB NOT NULL,
            config_after    JSONB NOT NULL,
            delta           JSONB NOT NULL DEFAULT '{}'::jsonb,
            metric_before   NUMERIC(12,6),
            metric_after    NUMERIC(12,6),
            uplift_pct      NUMERIC(8,4),
            applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            applied_by      TEXT NOT NULL DEFAULT 'system',
            rolled_back_at  TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_opt_history_agent
            ON agent_optimization_history(tenant_id, agent_id, applied_at DESC)
    """)


def downgrade() -> None:
    for t in [
        "agent_optimization_history",
        "improvement_results",
        "improvement_experiments",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
