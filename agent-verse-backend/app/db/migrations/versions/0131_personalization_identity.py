"""chat_personal_profiles + principals + identity_links — durable personalization & identity.

Backs Phase 11 personalization (per-principal tone / standing instructions /
preferences) and the Phase 3 dual-mode identity layer (unified principals across
channels) so both survive restarts and span workers, replacing the in-memory
defaults. Tenant-isolated via RLS like the other tenant tables; ids/tenant_id are
TEXT to match TenantContext.tenant_id and the principal ids the app generates.

Revision ID: 0131
Revises: 0130
"""

from __future__ import annotations

from alembic import op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None

_TABLES = ("chat_personal_profiles", "principals", "identity_links")


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = '{table}' AND policyname = '{table}_isolation'
            ) THEN
                CREATE POLICY {table}_isolation ON {table}
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_personal_profiles (
            principal_id          TEXT PRIMARY KEY,
            tenant_id             TEXT NOT NULL,
            tone                  TEXT,
            standing_instructions JSONB NOT NULL DEFAULT '[]'::jsonb,
            preferences           JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_personal_profiles_tenant "
        "ON chat_personal_profiles (tenant_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS principals (
            id           TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'individual',
            display_name TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_principals_tenant ON principals (tenant_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_links (
            tenant_id       TEXT NOT NULL,
            channel         TEXT NOT NULL,
            channel_user_id TEXT NOT NULL,
            principal_id    TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, channel, channel_user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_identity_links_principal "
        "ON identity_links (tenant_id, principal_id)"
    )
    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
    op.execute("DROP TABLE IF EXISTS identity_links")
    op.execute("DROP TABLE IF EXISTS principals")
    op.execute("DROP TABLE IF EXISTS chat_personal_profiles")
