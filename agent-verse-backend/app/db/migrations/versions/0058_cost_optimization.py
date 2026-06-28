"""model_pricing, budget_configs, and cost_ledger enhancements for cost optimization

Revision ID: 0058
Revises: 0057
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Table: model_pricing                                                 #
    # Central registry of LLM pricing so costs are DB-driven, not        #
    # hardcoded, and can be updated without a code deploy.                 #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_pricing (
            id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            model_id            TEXT NOT NULL UNIQUE,
            display_name        TEXT NOT NULL,
            provider            TEXT NOT NULL,
            input_usd_per_1m    NUMERIC(10,4) NOT NULL,
            output_usd_per_1m   NUMERIC(10,4) NOT NULL,
            cache_discount_pct  FLOAT NOT NULL DEFAULT 0.1,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_pricing_active
            ON model_pricing(provider, model_id) WHERE is_active = TRUE
    """)

    # Seed with current pricing (all values USD per 1M tokens)
    op.execute("""
        INSERT INTO model_pricing
            (model_id, display_name, provider, input_usd_per_1m, output_usd_per_1m)
        VALUES
            -- Anthropic
            ('claude-opus-4',          'Claude Opus 4',         'anthropic',    15.00,  75.00),
            ('claude-opus-4-8',        'Claude Opus 4.8',       'anthropic',    15.00,  75.00),
            ('claude-sonnet-4-5',      'Claude Sonnet 4.5',     'anthropic',     3.00,  15.00),
            ('claude-haiku-3-5',       'Claude Haiku 3.5',      'anthropic',     0.80,   4.00),
            ('claude-3-haiku-20240307','Claude 3 Haiku',        'anthropic',     0.25,   1.25),
            -- OpenAI
            ('gpt-4o',                 'GPT-4o',                'openai',        2.50,  10.00),
            ('gpt-4o-mini',            'GPT-4o Mini',           'openai',        0.15,   0.60),
            ('o1-preview',             'o1-preview',            'openai',       15.00,  60.00),
            ('o1-mini',                'o1-mini',               'openai',        3.00,  12.00),
            ('gpt-4-turbo',            'GPT-4 Turbo',           'openai',       10.00,  30.00),
            -- Gemini
            ('gemini-2.0-flash',       'Gemini 2.0 Flash',      'gemini',        0.075,  0.30),
            ('gemini-2.0-pro',         'Gemini 2.0 Pro',        'gemini',        3.50,  10.50),
            ('gemini-1.5-pro',         'Gemini 1.5 Pro',        'gemini',        1.25,   5.00),
            ('gemini-1.5-flash',       'Gemini 1.5 Flash',      'gemini',        0.075,  0.30)
        ON CONFLICT (model_id) DO NOTHING
    """)

    # ------------------------------------------------------------------ #
    # Table: budget_configs                                                #
    # Per-tenant budget limits with alert thresholds.                     #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS budget_configs (
            tenant_id               TEXT PRIMARY KEY,
            per_goal_usd            NUMERIC(10,4) NOT NULL DEFAULT 10.0,
            per_tenant_daily_usd    NUMERIC(10,4) NOT NULL DEFAULT 500.0,
            per_agent_daily_usd     JSONB NOT NULL DEFAULT '{}',
            alert_pct_thresholds    INTEGER[] NOT NULL DEFAULT '{50,75,90}',
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_budget_configs_tenant
            ON budget_configs(tenant_id)
    """)

    # ------------------------------------------------------------------ #
    # Table: cost_ledger                                                  #
    # Append-only cost record per LLM call or tool invocation.           #
    # Partitioned by month for query efficiency at scale.                 #
    # ------------------------------------------------------------------ #
    op.execute("""
        CREATE TABLE IF NOT EXISTS cost_ledger (
            id                  TEXT NOT NULL DEFAULT gen_random_uuid()::text,
            tenant_id           TEXT NOT NULL,
            goal_id             TEXT,
            agent_id            TEXT,
            project_id          TEXT,
            model               TEXT,
            prompt_tokens       INTEGER,
            completion_tokens   INTEGER,
            cost_usd            NUMERIC(12,8) NOT NULL DEFAULT 0,
            cost_type           TEXT NOT NULL DEFAULT 'llm',
            tags                JSONB DEFAULT '{}',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    # Monthly partitions for 2026 and 2027
    for yr in range(2026, 2028):
        for mo in range(1, 13):
            start = f"{yr}-{mo:02d}-01"
            next_mo = mo + 1 if mo < 12 else 1
            next_yr = yr if mo < 12 else yr + 1
            end = f"{next_yr}-{next_mo:02d}-01"
            tname = f"cost_ledger_{yr}_{mo:02d}"
            op.execute(
                f"CREATE TABLE IF NOT EXISTS {tname} "
                f"PARTITION OF cost_ledger "
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            )

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_ledger_tenant
            ON cost_ledger(tenant_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_ledger_goal
            ON cost_ledger(goal_id, created_at DESC) WHERE goal_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_ledger_agent
            ON cost_ledger(agent_id, created_at DESC) WHERE agent_id IS NOT NULL
    """)
    op.execute("ALTER TABLE cost_ledger ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY cost_ledger_isolation ON cost_ledger "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )

    # Enhance cost_ledger with any columns that may have been added in-flight
    # (safe to run even if cost_ledger just created above — IF NOT EXISTS guards)
    for col_ddl in [
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS agent_id TEXT",
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS project_id TEXT",
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS model TEXT",
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER",
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS completion_tokens INTEGER",
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS cost_type TEXT NOT NULL DEFAULT 'llm'",
        "ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '{}'",
    ]:
        try:
            op.execute(col_ddl)
        except Exception:
            pass  # Column already present — safe to ignore


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cost_ledger CASCADE")
    op.execute("DROP TABLE IF EXISTS budget_configs CASCADE")
    op.execute("DROP TABLE IF EXISTS model_pricing CASCADE")
