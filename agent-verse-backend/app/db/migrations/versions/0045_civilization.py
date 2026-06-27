"""Create all Agent Civilization tables with RLS and indexes."""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

TABLES = [
    ("civilizations", """
        CREATE TABLE IF NOT EXISTS civilizations (
            id         TEXT PRIMARY KEY,
            tenant_id  TEXT NOT NULL,
            name       TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'active',
            constitution JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """),
    ("civilization_agents", """
        CREATE TABLE IF NOT EXISTS civilization_agents (
            id               TEXT PRIMARY KEY,
            civilization_id  TEXT NOT NULL REFERENCES civilizations(id) ON DELETE CASCADE,
            tenant_id        TEXT NOT NULL,
            agent_id         TEXT NOT NULL,
            role             TEXT NOT NULL DEFAULT 'worker',
            parent_agent_id  TEXT,
            reputation       FLOAT NOT NULL DEFAULT 0.5,
            status           TEXT NOT NULL DEFAULT 'active',
            depth            INT NOT NULL DEFAULT 0,
            budget_usd       FLOAT NOT NULL DEFAULT 0,
            budget_spent_usd FLOAT NOT NULL DEFAULT 0,
            spawned_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            retired_at       TIMESTAMPTZ,
            last_active_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(civilization_id, agent_id)
        )
    """),
    ("spawn_requests", """
        CREATE TABLE IF NOT EXISTS spawn_requests (
            id                    TEXT PRIMARY KEY,
            civilization_id       TEXT NOT NULL REFERENCES civilizations(id) ON DELETE CASCADE,
            tenant_id             TEXT NOT NULL,
            requester_agent_id    TEXT NOT NULL,
            requested_capability  TEXT NOT NULL DEFAULT '',
            goal_text             TEXT NOT NULL DEFAULT '',
            decision              TEXT NOT NULL DEFAULT 'denied',
            reason                TEXT NOT NULL DEFAULT '',
            verdict               JSONB NOT NULL DEFAULT '{}',
            created_agent_id      TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """),
    ("blackboard_entries", """
        CREATE TABLE IF NOT EXISTS blackboard_entries (
            id               TEXT PRIMARY KEY,
            civilization_id  TEXT NOT NULL REFERENCES civilizations(id) ON DELETE CASCADE,
            tenant_id        TEXT NOT NULL,
            author_agent_id  TEXT NOT NULL,
            topic            TEXT NOT NULL DEFAULT '',
            content          TEXT NOT NULL DEFAULT '',
            confidence       FLOAT NOT NULL DEFAULT 0.8,
            refs             JSONB NOT NULL DEFAULT '[]',
            version          INT NOT NULL DEFAULT 1,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """),
    ("bus_messages", """
        CREATE TABLE IF NOT EXISTS bus_messages (
            id               TEXT PRIMARY KEY,
            civilization_id  TEXT NOT NULL REFERENCES civilizations(id) ON DELETE CASCADE,
            tenant_id        TEXT NOT NULL,
            from_agent_id    TEXT NOT NULL,
            topic            TEXT NOT NULL DEFAULT '',
            payload          JSONB NOT NULL DEFAULT '{}',
            ts               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """),
    ("civilization_learnings", """
        CREATE TABLE IF NOT EXISTS civilization_learnings (
            id                   TEXT PRIMARY KEY,
            civilization_id      TEXT NOT NULL REFERENCES civilizations(id) ON DELETE CASCADE,
            tenant_id            TEXT NOT NULL,
            candidate            TEXT NOT NULL DEFAULT '',
            source_agent_id      TEXT NOT NULL,
            status               TEXT NOT NULL DEFAULT 'candidate',
            eval_score           FLOAT,
            promoted_memory_id   TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at           TIMESTAMPTZ
        )
    """),
    ("civilization_events", """
        CREATE TABLE IF NOT EXISTS civilization_events (
            id               TEXT PRIMARY KEY,
            civilization_id  TEXT NOT NULL REFERENCES civilizations(id) ON DELETE CASCADE,
            tenant_id        TEXT NOT NULL,
            type             TEXT NOT NULL DEFAULT '',
            payload          JSONB NOT NULL DEFAULT '{}',
            ts               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """),
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_civilizations_tenant ON civilizations (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_civ_agents_civ ON civilization_agents (civilization_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_civ_agents_tenant ON civilization_agents (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_spawn_req_civ ON spawn_requests "
    "(civilization_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_spawn_req_tenant ON spawn_requests (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_blackboard_civ ON blackboard_entries (civilization_id, topic)",
    "CREATE INDEX IF NOT EXISTS ix_blackboard_tenant ON blackboard_entries (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_bus_messages_civ ON bus_messages (civilization_id, ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_bus_messages_tenant ON bus_messages (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_learnings_civ ON civilization_learnings "
    "(civilization_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_learnings_tenant ON civilization_learnings (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_civ_events_civ ON civilization_events "
    "(civilization_id, ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_civ_events_tenant ON civilization_events (tenant_id)",
]


def upgrade() -> None:
    for table_name, ddl in TABLES:
        op.execute(ddl)
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_tenant_isolation ON {table_name} "
            f"USING (tenant_id = current_setting('app.tenant_id', TRUE))"
        )
    for idx in INDEXES:
        op.execute(idx)
    # Additive nullable columns on audit_log (safe with immutability trigger which blocks
    # UPDATE/DELETE, not ALTER)
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS civilization_id TEXT")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS parent_agent_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS civilization_id")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS parent_agent_id")
    for table_name, _ in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
