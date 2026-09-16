"""agent scoped api keys persistence

Durable, tenant-isolated store for per-agent scoped API keys, which lived in an
in-memory AgentCredentialStore dict — created keys vanished on restart and diverged
across pods (distributed-scale audit X2). RLS enable+force+isolation like the other
tenant tables; key_hash is uniquely indexed for O(1) resolution.

Revision ID: 44a3062580a1
Revises: d26899b0a1e8
Create Date: 2026-09-16 13:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "44a3062580a1"
down_revision: str | None = "d26899b0a1e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_api_keys (
            key_id             TEXT PRIMARY KEY,
            tenant_id          TEXT NOT NULL,
            agent_id           TEXT NOT NULL,
            name               TEXT NOT NULL DEFAULT '',
            key_hash           TEXT NOT NULL,
            allowed_tools      JSONB,
            denied_tools       JSONB NOT NULL DEFAULT '[]'::jsonb,
            allowed_connectors JSONB,
            expires_at         DOUBLE PRECISION,
            created_by         TEXT NOT NULL DEFAULT '',
            created_at         DOUBLE PRECISION NOT NULL DEFAULT extract(epoch FROM now()),
            last_used_at       DOUBLE PRECISION,
            is_active          BOOLEAN NOT NULL DEFAULT TRUE,
            use_count          INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_api_keys_key_hash "
        "ON agent_api_keys (key_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_api_keys_agent "
        "ON agent_api_keys (tenant_id, agent_id)"
    )
    op.execute("ALTER TABLE agent_api_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_api_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'agent_api_keys' AND policyname = 'agent_api_keys_isolation'
            ) THEN
                CREATE POLICY agent_api_keys_isolation ON agent_api_keys
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_api_keys_isolation ON agent_api_keys")
    op.execute("DROP TABLE IF EXISTS agent_api_keys")
