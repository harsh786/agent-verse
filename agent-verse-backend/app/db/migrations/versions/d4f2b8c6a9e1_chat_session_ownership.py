"""Chat sessions are private to their owner (CHAT-SEC-1).

* ``chat_sessions.owner_principal``: the principal that created the session —
  ``user:<user id>`` for a signed-in person, ``key:<api key id>`` for an API key
  with no person behind it. NULL for a session created by a channel or before
  this revision: such a session is reachable only through the audited admin
  routes until an admin assigns it to a person.
* Backfill: a session that already records its person (``owner_user_id``, set
  since c3e8a1f5b7d2) becomes ``user:<owner_user_id>``, in batches. The UPDATE
  must see every tenant's rows, so it runs only when the migrating role bypasses
  RLS (superuser or BYPASSRLS); otherwise it is skipped with a warning and those
  sessions stay unowned (admin-only, never visible to another person) until an
  admin assigns them.
* ``ix_chat_sessions_owner_keyset`` (CONCURRENTLY): the keyset index of a
  principal's own session list.
* Restrictive RLS policies on ``chat_sessions``, ``chat_messages`` and
  ``chat_artifacts``: when a transaction sets ``app.chat_principal`` (every
  request-scoped query does), only that principal's sessions — or with
  ``'unowned'`` only the sessions with no owner — and their messages and
  artifacts are visible or writable. A transaction that does not set it (an
  internal path already authorized, the maintenance jobs) is not narrowed: the
  explicit owner predicate in each query stays the primary guard.

Revision ID: d4f2b8c6a9e1
Revises: c3e8a1f5b7d2
Create Date: 2026-10-06
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f2b8c6a9e1"
down_revision: str | None = "c3e8a1f5b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")
_BACKFILL_BATCH = 5000

_GUC = "current_setting('app.chat_principal', true)"
# No principal set: not narrowed. Otherwise the row's owner must be that
# principal ('unowned' selects the sessions with no owner).
_OWNER_OK = (
    f"(coalesce({_GUC}, '') = '' "
    f"OR owner_principal IS NOT DISTINCT FROM nullif({_GUC}, 'unowned'))"
)


def _child_ok(table: str) -> str:
    """A message/artifact is visible when its session is (chat_sessions' own RLS)."""
    nullable = " OR session_id IS NULL" if table == "chat_artifacts" else ""
    return (
        f"(coalesce({_GUC}, '') = ''{nullable} OR EXISTS (SELECT 1 FROM chat_sessions s "
        f"WHERE s.id = {table}.session_id AND s.tenant_id = {table}.tenant_id))"
    )


def _exists(table: str) -> bool:
    found = op.get_bind().execute(sa.text("SELECT to_regclass(:t)"), {"t": table}).scalar()
    return found is not None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS owner_principal VARCHAR(256)")
    op.execute("DROP POLICY IF EXISTS chat_sessions_owner ON chat_sessions")
    op.execute(
        f"CREATE POLICY chat_sessions_owner ON chat_sessions AS RESTRICTIVE FOR ALL "
        f"USING {_OWNER_OK} WITH CHECK {_OWNER_OK}"
    )
    for table in ("chat_messages", "chat_artifacts"):
        if not _exists(table):
            continue
        op.execute(f"DROP POLICY IF EXISTS {table}_session_owner ON {table}")
        op.execute(
            f"CREATE POLICY {table}_session_owner ON {table} AS RESTRICTIVE FOR ALL "
            f"USING {_child_ok(table)} WITH CHECK {_child_ok(table)}"
        )
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        can_see_all = bind.execute(
            sa.text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
        if can_see_all:
            while True:
                done = bind.execute(
                    sa.text(
                        "UPDATE chat_sessions SET owner_principal = 'user:' || owner_user_id "
                        "WHERE id IN (SELECT id FROM chat_sessions WHERE owner_user_id IS NOT "
                        "NULL AND owner_principal IS NULL LIMIT :n)"
                    ),
                    {"n": _BACKFILL_BATCH},
                ).rowcount
                if not done or done < _BACKFILL_BATCH:
                    break
        else:
            _log.warning(
                "chat_sessions owner backfill skipped: the migrating role cannot bypass RLS. "
                "Sessions created before this revision stay unowned (admin-only) until an "
                "admin assigns them (POST /chat/admin/sessions/{id}/assign)."
            )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_sessions_owner_keyset "
            "ON chat_sessions (tenant_id, owner_principal, updated_at DESC, id DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_sessions_owner_keyset")
    for table in ("chat_messages", "chat_artifacts"):
        if _exists(table):
            op.execute(f"DROP POLICY IF EXISTS {table}_session_owner ON {table}")
    op.execute("DROP POLICY IF EXISTS chat_sessions_owner ON chat_sessions")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS owner_principal")
