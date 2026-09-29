"""chat_channel_sessions + chat_principal_sessions: durable gateway conversations.

``ChatService`` kept the (tenant, channel, channel_user_id) -> chat session map
and the linked-identity principal -> chat session map in per-process dicts. A
restart, or a second API replica receiving the next Telegram/WhatsApp webhook,
forgot them and started a brand-new session, forking the user's conversation.

Both maps now live here. ``PostgresChatRepository.resolve_channel_session``
creates the session and claims the mapping in one transaction with
``INSERT ... ON CONFLICT DO NOTHING`` on the primary key, so replicas racing on a
user's first message converge on one session. Deleting a chat session cascades
its mapping rows away.

Tenant-isolated with ENABLE + FORCE RLS on the TEXT ``tenant_id`` (matching
``chat_sessions``), policy ``<table>_tenant_isolation``.

Revision ID: e5c1a9d3b7f2
Revises: a9c4e2f7b1d3
"""

from __future__ import annotations

from alembic import op

revision = "e5c1a9d3b7f2"
down_revision = "a9c4e2f7b1d3"
branch_labels = None
depends_on = None

_TABLES = ("chat_channel_sessions", "chat_principal_sessions")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_channel_sessions (
            tenant_id        TEXT NOT NULL,
            channel          TEXT NOT NULL,
            channel_user_id  TEXT NOT NULL,
            chat_session_id  VARCHAR(32) NOT NULL
                REFERENCES chat_sessions (id) ON DELETE CASCADE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_chat_channel_sessions
                PRIMARY KEY (tenant_id, channel, channel_user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_channel_sessions_session "
        "ON chat_channel_sessions (chat_session_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_principal_sessions (
            tenant_id        TEXT NOT NULL,
            principal_id     TEXT NOT NULL,
            chat_session_id  VARCHAR(32) NOT NULL
                REFERENCES chat_sessions (id) ON DELETE CASCADE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_chat_principal_sessions
                PRIMARY KEY (tenant_id, principal_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_principal_sessions_session "
        "ON chat_principal_sessions (chat_session_id)"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
