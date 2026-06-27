# app/db/migrations/versions/0043_debate_audit.py
"""Debate session audit trail tables for P3.1."""

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op
    op.execute("""
        CREATE TABLE IF NOT EXISTS debate_sessions (
            id              TEXT PRIMARY KEY,
            goal_id         TEXT NOT NULL DEFAULT '',
            tenant_id       TEXT NOT NULL,
            original_goal   TEXT NOT NULL DEFAULT '',
            consensus       TEXT NOT NULL DEFAULT '',
            confidence      FLOAT NOT NULL DEFAULT 0.0,
            rounds          INT NOT NULL DEFAULT 1,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at    TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS debate_proposals (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL REFERENCES debate_sessions(id) ON DELETE CASCADE,
            tenant_id       TEXT NOT NULL,
            agent_role      TEXT NOT NULL DEFAULT 'agent',
            proposal_text   TEXT NOT NULL,
            critique_text   TEXT NOT NULL DEFAULT '',
            vote            TEXT NOT NULL DEFAULT 'abstain',
            round_number    INT NOT NULL DEFAULT 1,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_debate_sessions_goal ON debate_sessions (goal_id, tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_debate_proposals_session ON debate_proposals (session_id)")


def downgrade() -> None:
    from alembic import op
    op.execute("DROP TABLE IF EXISTS debate_proposals")
    op.execute("DROP TABLE IF EXISTS debate_sessions")
