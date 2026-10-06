"""Index for the chat session TTL purge (CHAT-SEC-3).

``ix_chat_sessions_ttl_expiry (ttl_days, updated_at, id)`` over the sessions
that can expire (``ttl_days`` set, not pinned), built CONCURRENTLY. The purge
(app.chat.retention) reads the distinct ``ttl_days`` values with a loose index
scan and each value's expired sessions as an index range scan, so it never
walks the whole table.

Revision ID: e9a3c5d7f1b2
Revises: d4f2b8c6a9e1
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e9a3c5d7f1b2"
down_revision: str | None = "d4f2b8c6a9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chat_sessions_ttl_expiry "
            "ON chat_sessions (ttl_days, updated_at, id) "
            "WHERE ttl_days IS NOT NULL AND pinned IS FALSE"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_chat_sessions_ttl_expiry")
