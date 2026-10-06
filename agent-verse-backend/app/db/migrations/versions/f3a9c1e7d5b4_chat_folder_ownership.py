"""Chat folders are durable and private to their owner (CHAT-D-1).

Folders used to live only in one process's memory (lost on restart, invisible
to other replicas). They are now rows of ``chat_session_folders`` (created in
0105, tenant RLS since b4c5d6e7f8a9), owned like chat sessions (CHAT-SEC-1):

* ``owner_principal``: ``user:<id>`` or ``key:<api key id>``. A folder older
  than this revision has none and is reachable by no route (folders were never
  written to the table before, so none are expected).
* ``updated_at``: changes on rename/recolor.
* Restrictive RLS policy ``chat_session_folders_owner``: when a transaction sets
  ``app.chat_principal``, only that principal's folders are visible or writable
  (the same rule as ``chat_sessions_owner``).
* ``ix_chat_session_folders_owner (tenant_id, owner_principal, position,
  created_at, id)``: the owner's folder list, an index range scan.
* ``idx_chat_sessions_folder (folder_id)`` (created in 0105; ensured here): a
  deleted folder's sessions are unfiled by index, not by a table scan. There is
  no foreign key: the application unfiles them in the deleting transaction, and
  filing locks the folder row (``FOR KEY SHARE``) so the two serialize.
* Sessions filed into a folder that was never persisted (the in-memory era) are
  unfiled, in batches, so they show up again. The UPDATE must see every
  tenant's rows, so it runs only when the migrating role bypasses RLS; otherwise
  it is skipped with a warning (the UI lists such sessions as unfiled anyway).

Revision ID: f3a9c1e7d5b4
Revises: e9a3c5d7f1b2
Create Date: 2026-10-06
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c1e7d5b4"
down_revision: str | None = "e9a3c5d7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")
_BATCH = 5000

_GUC = "current_setting('app.chat_principal', true)"
_OWNER_OK = (
    f"(coalesce({_GUC}, '') = '' "
    f"OR owner_principal IS NOT DISTINCT FROM nullif({_GUC}, 'unowned'))"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chat_session_folders ADD COLUMN IF NOT EXISTS owner_principal VARCHAR(256)"
    )
    op.execute(
        "ALTER TABLE chat_session_folders ADD COLUMN IF NOT EXISTS updated_at "
        "TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute("DROP POLICY IF EXISTS chat_session_folders_owner ON chat_session_folders")
    op.execute(
        "CREATE POLICY chat_session_folders_owner ON chat_session_folders AS RESTRICTIVE "
        f"FOR ALL USING {_OWNER_OK} WITH CHECK {_OWNER_OK}"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_session_folders_owner "
            "ON chat_session_folders (tenant_id, owner_principal, position, created_at, id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_sessions_folder "
            "ON chat_sessions (folder_id)"
        )
        bind = op.get_bind()
        can_see_all = bind.execute(
            sa.text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
        if not can_see_all:
            _log.warning(
                "chat_sessions dangling folder_id cleanup skipped: the migrating role cannot "
                "bypass RLS (such sessions are listed as unfiled by the UI)."
            )
            return
        while True:
            done = bind.execute(
                sa.text(
                    "UPDATE chat_sessions SET folder_id = NULL WHERE id IN ("
                    "SELECT s.id FROM chat_sessions s WHERE s.folder_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM chat_session_folders f "
                    "WHERE f.id = s.folder_id AND f.tenant_id = s.tenant_id) LIMIT :n)"
                ),
                {"n": _BATCH},
            ).rowcount
            if not done or done < _BATCH:
                break


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_session_folders_owner")
    op.execute("DROP POLICY IF EXISTS chat_session_folders_owner ON chat_session_folders")
    op.execute("ALTER TABLE chat_session_folders DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE chat_session_folders DROP COLUMN IF EXISTS owner_principal")
