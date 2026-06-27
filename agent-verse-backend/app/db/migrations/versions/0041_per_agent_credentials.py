# app/db/migrations/versions/0041_per_agent_credentials.py
"""Per-agent connector credentials table for P2.7."""

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_connector_credentials (
            id              TEXT PRIMARY KEY,
            agent_id        TEXT NOT NULL,
            connector_id    TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            secret_ref      TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(agent_id, connector_id, tenant_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_creds_agent ON agent_connector_credentials (agent_id, tenant_id)")


def downgrade() -> None:
    from alembic import op
    op.execute("DROP INDEX IF EXISTS ix_agent_creds_agent")
    op.execute("DROP TABLE IF EXISTS agent_connector_credentials")
