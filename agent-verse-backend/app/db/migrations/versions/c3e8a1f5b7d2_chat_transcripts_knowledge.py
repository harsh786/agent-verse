"""Chat transcripts as knowledge: tenant switch, per-user opt-in, session owner (CHAT-KB).

Owner decision 7 (2026-10-06): a user's chats may become knowledge only with
double consent, both off by default:

* ``tenants.chat_transcripts_kb_enabled`` (default FALSE): the tenant admin's
  switch that enables the ``chat_transcript`` kind of ``agent_generated`` Sources.
* ``chat_kb_consents``: one row per (tenant, user) holding that person's own,
  revocable opt-in. RLS enabled and FORCEd.
* ``chat_sessions.owner_user_id``: the person who created the session (an SSO
  user, ``TenantContext.user_id``). Only owned sessions can ever be indexed, and
  only under their owner's consent; sessions created by an API key, a channel
  (Telegram, Slack ...) or before this revision have no owner and never are.

Indexes (CONCURRENTLY, no write lock): a partial keyset index over owned
sessions ``(tenant_id, updated_at, id)``, and in every per-dimension chunk table
that exists a partial index over the transcript chunks' ``origin.user_id`` so a
revocation or erasure finds a user's transcripts without scanning the tenant.

Revision ID: c3e8a1f5b7d2
Revises: a12c4e6f8b10
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a1f5b7d2"
down_revision: str | None = "a12c4e6f8b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHUNK_TABLES = tuple(f"knowledge_chunks_{dim}" for dim in (768, 1024, 1536, 2048, 3072))


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
        "chat_transcripts_kb_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(64)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_kb_consents (
            tenant_id    VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id      VARCHAR(64) NOT NULL,
            opted_in     BOOLEAN     NOT NULL DEFAULT FALSE,
            opted_in_at  TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        )
        """
    )
    op.execute("ALTER TABLE chat_kb_consents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_kb_consents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'chat_kb_consents'
                  AND policyname = 'chat_kb_consents_tenant_isolation'
            ) THEN
                CREATE POLICY chat_kb_consents_tenant_isolation ON chat_kb_consents
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_sessions_owned_keyset "
            "ON chat_sessions (tenant_id, updated_at, id) WHERE owner_user_id IS NOT NULL"
        )
        bind = op.get_bind()
        for table in _CHUNK_TABLES:
            if bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None:
                continue
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_{table}_chat_transcript_user "
                f"ON {table} (tenant_id, (metadata->'origin'->>'user_id')) "
                "WHERE metadata->'origin'->>'kind' = 'chat_transcript'"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for table in _CHUNK_TABLES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS ix_{table}_chat_transcript_user")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_sessions_owned_keyset")
    op.execute("DROP TABLE IF EXISTS chat_kb_consents")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS owner_user_id")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS chat_transcripts_kb_enabled")
