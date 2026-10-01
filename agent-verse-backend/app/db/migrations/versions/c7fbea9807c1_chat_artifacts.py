"""chat_artifacts: durable chat-generated documents and session artifacts (ORG-42).

``ChatArtifactStore`` kept generated document bytes in an unbounded per-process
dict and ``ChatService`` kept session artifacts in ``_artifacts``: a download
404'd on every other replica and after a restart, and memory grew without bound.

Migration 0105 created a ``chat_artifacts`` table that no code ever wrote (RLS was
forced on it by b4c5d6e7f8a9). This revision evolves it so both kinds live there,
one row per artifact:

* ``kind = 'document'`` -- a generated file (pdf/md/csv/...): bytes in
  ``content``, ``mime``, no session required, and an ``expires_at`` (retention)
  after which reads ignore it and the ``purge_expired_chat_artifacts`` beat task
  deletes it in batches.
* ``kind = 'snippet'`` -- a saved session artifact (code/text); it belongs to a
  chat session and is deleted with it (``ON DELETE CASCADE``).

``content`` becomes BYTEA (existing text converted as UTF-8). The application caps
``size_bytes`` before writing. The FK is added ``NOT VALID`` so a pre-existing
orphan row cannot block the upgrade while every new row is checked.

Revision ID: c7fbea9807c1
Revises: cf87de8eae52
"""

from __future__ import annotations

from alembic import op

revision = "c7fbea9807c1"
down_revision = "cf87de8eae52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN content DROP DEFAULT")
    op.execute(
        "ALTER TABLE chat_artifacts ALTER COLUMN content TYPE BYTEA "
        "USING convert_to(content, 'UTF8')"
    )
    op.execute("ALTER TABLE chat_artifacts ADD COLUMN IF NOT EXISTS kind TEXT")
    op.execute("UPDATE chat_artifacts SET kind = 'snippet' WHERE kind IS NULL")
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN kind SET NOT NULL")
    op.execute(
        "ALTER TABLE chat_artifacts ADD COLUMN IF NOT EXISTS mime TEXT NOT NULL "
        "DEFAULT 'application/octet-stream'"
    )
    op.execute(
        "ALTER TABLE chat_artifacts ADD COLUMN IF NOT EXISTS size_bytes INTEGER NOT NULL DEFAULT 0"
    )
    op.execute("UPDATE chat_artifacts SET size_bytes = octet_length(content)")
    op.execute("ALTER TABLE chat_artifacts ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN session_id DROP NOT NULL")
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN language DROP NOT NULL")
    op.execute(
        "ALTER TABLE chat_artifacts ADD CONSTRAINT ck_chat_artifacts_kind "
        "CHECK (kind IN ('document', 'snippet'))"
    )
    op.execute(
        "ALTER TABLE chat_artifacts ADD CONSTRAINT ck_chat_artifacts_size CHECK (size_bytes >= 0)"
    )
    op.execute(
        "ALTER TABLE chat_artifacts ADD CONSTRAINT ck_chat_artifacts_snippet_session "
        "CHECK (kind <> 'snippet' OR session_id IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE chat_artifacts ADD CONSTRAINT fk_chat_artifacts_session "
        "FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE NOT VALID"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_artifacts_tenant_session "
        "ON chat_artifacts (tenant_id, session_id, created_at) "
        "WHERE session_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_artifacts_expires "
        "ON chat_artifacts (expires_at) WHERE expires_at IS NOT NULL"
    )
    # RLS is already ENABLE + FORCE with chat_artifacts_tenant_isolation
    # (b4c5d6e7f8a9); restated idempotently so the table is never left open.
    op.execute("ALTER TABLE chat_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_artifacts FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chat_artifacts_expires")
    op.execute("DROP INDEX IF EXISTS ix_chat_artifacts_tenant_session")
    op.execute("ALTER TABLE chat_artifacts DROP CONSTRAINT IF EXISTS fk_chat_artifacts_session")
    op.execute(
        "ALTER TABLE chat_artifacts DROP CONSTRAINT IF EXISTS ck_chat_artifacts_snippet_session"
    )
    op.execute("ALTER TABLE chat_artifacts DROP CONSTRAINT IF EXISTS ck_chat_artifacts_size")
    op.execute("ALTER TABLE chat_artifacts DROP CONSTRAINT IF EXISTS ck_chat_artifacts_kind")
    # Generated documents have no session and binary content: they cannot exist
    # in the 0105 shape.
    op.execute("DELETE FROM chat_artifacts WHERE kind = 'document' OR session_id IS NULL")
    op.execute("UPDATE chat_artifacts SET language = 'text' WHERE language IS NULL")
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN language SET NOT NULL")
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN session_id SET NOT NULL")
    op.execute("ALTER TABLE chat_artifacts DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE chat_artifacts DROP COLUMN IF EXISTS size_bytes")
    op.execute("ALTER TABLE chat_artifacts DROP COLUMN IF EXISTS mime")
    op.execute("ALTER TABLE chat_artifacts DROP COLUMN IF EXISTS kind")
    op.execute(
        "ALTER TABLE chat_artifacts ALTER COLUMN content TYPE TEXT "
        "USING convert_from(content, 'UTF8')"
    )
    op.execute("ALTER TABLE chat_artifacts ALTER COLUMN content SET DEFAULT ''")
