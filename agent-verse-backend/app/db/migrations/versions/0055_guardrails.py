"""Create guardrail_configs and guardrail_violations tables with partitioning."""

from alembic import op

revision = "0055"
down_revision = "0054"   # actual latest migration in this repo
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── guardrail_configs ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_configs (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
            tenant_id   TEXT NOT NULL,
            agent_id    TEXT,
            name        TEXT NOT NULL,
            layer       TEXT NOT NULL DEFAULT 'goal'
                        CHECK (layer IN (
                            'goal','plan','step','tool_args','tool_output','final'
                        )),
            rule_type   TEXT NOT NULL DEFAULT 'injection'
                        CHECK (rule_type IN (
                            'injection','pii','dangerous_pattern',
                            'content_policy','custom_regex','llm_judge'
                        )),
            config      JSONB NOT NULL DEFAULT '{}',
            severity    TEXT NOT NULL DEFAULT 'high'
                        CHECK (severity IN ('critical','high','medium','low','info')),
            action      TEXT NOT NULL DEFAULT 'block'
                        CHECK (action IN ('block','warn','redact','log')),
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_configs_tenant"
        " ON guardrail_configs (tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_configs_agent"
        " ON guardrail_configs (agent_id)"
        " WHERE agent_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_configs_layer"
        " ON guardrail_configs (layer, tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_configs_config_gin"
        " ON guardrail_configs USING GIN (config)"
    )
    op.execute("ALTER TABLE guardrail_configs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guardrail_configs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY guardrail_configs_tenant_isolation ON guardrail_configs
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)

    # ── guardrail_violations (partitioned by month) ───────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations (
            id              TEXT NOT NULL DEFAULT gen_random_uuid()::TEXT,
            tenant_id       TEXT NOT NULL,
            goal_id         TEXT,
            agent_id        TEXT,
            rule_id         TEXT,
            layer           TEXT NOT NULL,
            violation_type  TEXT NOT NULL,
            severity        TEXT NOT NULL
                            CHECK (severity IN ('critical','high','medium','low','info')),
            pattern_matched TEXT,
            location        TEXT,
            action_taken    TEXT NOT NULL DEFAULT 'logged',
            content_preview TEXT,   -- first 200 chars, PII stripped
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        ) PARTITION BY RANGE (created_at)
    """)

    # Pre-create partitions for current year (Jan–Dec)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_01
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_02
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_03
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_04
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_05
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_06
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-06-01') TO ('2026-07-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_07
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-07-01') TO ('2026-08-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_08
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-08-01') TO ('2026-09-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_09
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-09-01') TO ('2026-10-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_10
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-10-01') TO ('2026-11-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_11
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-11-01') TO ('2026-12-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_2026_12
            PARTITION OF guardrail_violations
            FOR VALUES FROM ('2026-12-01') TO ('2027-01-01')
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS guardrail_violations_default
            PARTITION OF guardrail_violations DEFAULT
    """)

    # Indexes on the parent table (inherited by all partitions)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_violations_tenant_ts"
        " ON guardrail_violations (tenant_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_violations_goal"
        " ON guardrail_violations (goal_id)"
        " WHERE goal_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_guardrail_violations_severity"
        " ON guardrail_violations (severity, created_at DESC)"
    )

    op.execute("ALTER TABLE guardrail_violations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guardrail_violations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY guardrail_violations_tenant_isolation ON guardrail_violations
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS guardrail_violations CASCADE")
    op.execute("DROP TABLE IF EXISTS guardrail_configs CASCADE")
