"""magentic human review tokens persistence

Durable, tenant-isolated store for one-time Magentic human-review tokens/decisions,
which lived in in-memory dicts — a token issued on one pod could not be consumed on
another, and the one-time-consume check was per-pod (double-submit possible across
pods) (distributed-scale audit X8). RLS enable+force+isolation.

Revision ID: 619ba480ee3a
Revises: 9f4636d0feaf
Create Date: 2026-09-16 14:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "619ba480ee3a"
down_revision: str | None = "9f4636d0feaf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS magentic_review_tokens (
            tenant_id   TEXT NOT NULL,
            session_id  TEXT NOT NULL,
            token_hash  TEXT NOT NULL,
            approved    BOOLEAN,
            safe_note   TEXT,
            consumed    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            consumed_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, session_id)
        )
        """
    )
    op.execute("ALTER TABLE magentic_review_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE magentic_review_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = 'magentic_review_tokens'
                  AND policyname = 'magentic_review_tokens_isolation'
            ) THEN
                CREATE POLICY magentic_review_tokens_isolation ON magentic_review_tokens
                    USING (tenant_id = current_setting('app.tenant_id', true))
                    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS magentic_review_tokens_isolation ON magentic_review_tokens"
    )
    op.execute("DROP TABLE IF EXISTS magentic_review_tokens")
