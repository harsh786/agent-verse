"""Add agent_credentials table and domain identity columns on agents.

Adds:
- agent_credentials table: RSA public keys, JWT signing credentials per agent
- agents.domain_context: TEXT domain tag (legal, healthcare, finance, …)
- agents.domain_metadata: JSONB domain-specific identity attributes (bar number, NPI, …)
"""

from alembic import op

revision = "0053"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE agent_credentials (
            id              TEXT PRIMARY KEY,
            agent_id        TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            tenant_id       TEXT NOT NULL,
            key_type        TEXT NOT NULL DEFAULT 'service_account',
            key_id          TEXT NOT NULL UNIQUE,
            public_key      TEXT,
            private_key_ref TEXT,
            scopes          TEXT[] NOT NULL DEFAULT '{}',
            expires_at      TIMESTAMPTZ,
            revoked_at      TIMESTAMPTZ,
            last_used_at    TIMESTAMPTZ,
            created_by      TEXT NOT NULL,
            metadata        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_agent_credentials_agent  ON agent_credentials(agent_id)"
    )
    op.execute(
        "CREATE INDEX ix_agent_credentials_key_id ON agent_credentials(key_id)"
    )
    op.execute(
        "CREATE INDEX ix_agent_credentials_tenant ON agent_credentials(tenant_id)"
    )
    op.execute("ALTER TABLE agent_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_credentials FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY agent_credentials_tenant ON agent_credentials
            USING      (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)
    op.execute("""
        ALTER TABLE agents
            ADD COLUMN IF NOT EXISTS domain_context TEXT NOT NULL DEFAULT 'general',
            ADD COLUMN IF NOT EXISTS domain_metadata JSONB NOT NULL DEFAULT '{}'
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agents_domain_metadata "
        "ON agents USING GIN(domain_metadata)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agents_domain_metadata")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS domain_metadata")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS domain_context")
    op.execute("DROP TABLE IF EXISTS agent_credentials CASCADE")
