"""chat_sessions + chat_messages — durable chat persistence.

The chat subsystem was in-memory only (lost on restart, not multi-worker safe).
These tables back the conversation so sessions and history survive restarts,
span workers, and can be recalled after long delays — the foundation for the
world-class chat platform (Phase 0.3d). Tenant-isolated via RLS like the other
tenant tables; tenant_id is TEXT to match TenantContext.tenant_id.

Revision ID: 0130
Revises: 0129
"""

from __future__ import annotations

from alembic import op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None

_TABLES = ("chat_sessions", "chat_messages")


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
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id                    TEXT PRIMARY KEY,
            tenant_id             TEXT NOT NULL,
            title                 TEXT NOT NULL DEFAULT 'New Chat',
            pinned                BOOLEAN NOT NULL DEFAULT FALSE,
            ttl_days              INTEGER,
            system_prompt         TEXT,
            agent_id              TEXT,
            folder_id             TEXT,
            show_reasoning        BOOLEAN NOT NULL DEFAULT FALSE,
            proactive_suggestions BOOLEAN NOT NULL DEFAULT TRUE,
            preferred_model       TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_updated "
        "ON chat_sessions (tenant_id, updated_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id                 TEXT PRIMARY KEY,
            session_id         TEXT NOT NULL,
            tenant_id          TEXT NOT NULL,
            role               TEXT NOT NULL,
            content            TEXT NOT NULL DEFAULT '',
            metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
            branch_id          TEXT,
            parent_message_id  TEXT,
            goal_id            TEXT,
            intent             TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created "
        "ON chat_messages (session_id, created_at)"
    )
    for table in _TABLES:
        _force_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_sessions")
