"""Durable feedback on chat replies (CHAT-D-2).

Thumbs up/down and a comment on a chat message used to be written into an
in-memory copy of the message and lost. ``chat_message_feedback`` holds one row
per (message, rater):

* ``owner_principal``: who rated (``user:<id>`` / ``key:<api key id>``); chats
  are private, so the rater is the session's owner.
* ``UNIQUE (message_id, owner_principal)``: rating again edits the same row
  (idempotent per person). It also indexes the ``ON DELETE CASCADE`` from
  ``chat_messages``, so deleting, pruning or expiring a message removes its
  feedback without a scan.
* ``rating`` in -1..1, ``comment`` at most 4000 characters (CHECKs).
* RLS: tenant isolation, plus the restrictive owner policy of the chat tables
  (``app.chat_principal``), so a transaction scoped to one principal never
  sees or writes another's feedback.

Revision ID: a6c2e8f4b0d3
Revises: f3a9c1e7d5b4
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a6c2e8f4b0d3"
down_revision: str | None = "f3a9c1e7d5b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = "tenant_id = current_setting('app.tenant_id', true)"
_GUC = "current_setting('app.chat_principal', true)"
_OWNER_OK = (
    f"(coalesce({_GUC}, '') = '' "
    f"OR owner_principal IS NOT DISTINCT FROM nullif({_GUC}, 'unowned'))"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_message_feedback (
            id              VARCHAR(64) PRIMARY KEY,
            tenant_id       VARCHAR(64) NOT NULL,
            session_id      VARCHAR(64) NOT NULL,
            message_id      VARCHAR(64) NOT NULL
                REFERENCES chat_messages (id) ON DELETE CASCADE,
            owner_principal VARCHAR(256) NOT NULL,
            rating          SMALLINT NOT NULL,
            comment         TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_chat_message_feedback_rater UNIQUE (message_id, owner_principal),
            CONSTRAINT ck_chat_message_feedback_rating CHECK (rating BETWEEN -1 AND 1),
            CONSTRAINT ck_chat_message_feedback_comment
                CHECK (comment IS NULL OR char_length(comment) <= 4000)
        )
        """
    )
    op.execute("ALTER TABLE chat_message_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_message_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS chat_message_feedback_tenant_isolation ON chat_message_feedback"
    )
    op.execute(
        "CREATE POLICY chat_message_feedback_tenant_isolation ON chat_message_feedback "
        f"USING ({_TENANT}) WITH CHECK ({_TENANT})"
    )
    op.execute("DROP POLICY IF EXISTS chat_message_feedback_owner ON chat_message_feedback")
    op.execute(
        "CREATE POLICY chat_message_feedback_owner ON chat_message_feedback AS RESTRICTIVE "
        f"FOR ALL USING {_OWNER_OK} WITH CHECK {_OWNER_OK}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_message_feedback")
