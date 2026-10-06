"""Purge Memory 2.0 rows that were only flagged deleted (a10-F238-05)

``DELETE /memory-v2/{id}`` used to keep the ``long_term_memory`` row and set
``lifecycle_state: deleted`` inside its JSON ``content``. Long-term recall and
listing never read that flag, so a memory the user deleted (a GDPR erasure)
kept being recalled into planner prompts. The endpoint now deletes the row;
this removes the rows earlier deletes left behind, and the ``memory_conflicts``
rows that name them.

FORCE RLS hides every row from the migration role (no tenant GUC), so it is
lifted for the purge and restored afterwards (only if it was on).

Revision ID: d5b7f9a1c3e5
Revises: c3a1e5f7b9d1
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "d5b7f9a1c3e5"
down_revision: str | Sequence[str] | None = "c3a1e5f7b9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only v2 rows hold JSON content; the CASE keeps the cast off every other row.
PURGE_CONFLICTS_SQL = """
DELETE FROM memory_conflicts c
 USING long_term_memory m
 WHERE m.memory_type = 'memory_v2'
   AND (CASE WHEN m.memory_type = 'memory_v2'
             THEN m.content::jsonb ->> 'lifecycle_state' END) = 'deleted'
   AND c.tenant_id = m.tenant_id
   AND (c.memory_id_a = m.id OR c.memory_id_b = m.id)
"""
PURGE_MEMORIES_SQL = """
DELETE FROM long_term_memory
 WHERE memory_type = 'memory_v2'
   AND (CASE WHEN memory_type = 'memory_v2'
             THEN content::jsonb ->> 'lifecycle_state' END) = 'deleted'
"""
_TABLES = ("long_term_memory", "memory_conflicts")


def upgrade() -> None:
    bind = op.get_bind()
    forced = {
        t
        for t in _TABLES
        if bind.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:t)"),
            {"t": t},
        ).scalar()
    }
    for t in forced:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
    op.execute(PURGE_CONFLICTS_SQL)
    op.execute(PURGE_MEMORIES_SQL)
    for t in forced:
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Deleted memories are not restored."""
