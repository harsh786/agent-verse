"""Add civilization history tables: reputation_history and constitution_history.

Revision ID: 0063
Revises: 0062
Create Date: 2026-06-28
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reputation time-series (EWMA snapshots every governor tick)
    op.execute("""
        CREATE TABLE IF NOT EXISTS civilization_reputation_history (
            id              TEXT PRIMARY KEY,
            civilization_id TEXT NOT NULL,
            agent_id        TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            reputation      FLOAT NOT NULL,
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_civ_rep_hist_civ_agent "
        "ON civilization_reputation_history (civilization_id, agent_id, recorded_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_civ_rep_hist_tenant "
        "ON civilization_reputation_history (tenant_id)"
    )

    # Constitution version history
    op.execute("""
        CREATE TABLE IF NOT EXISTS civilization_constitution_history (
            id              TEXT PRIMARY KEY,
            civilization_id TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            constitution    JSONB NOT NULL,
            changed_by      TEXT NOT NULL DEFAULT 'user',
            change_reason   TEXT NOT NULL DEFAULT '',
            changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_civ_const_hist_civ "
        "ON civilization_constitution_history (civilization_id, changed_at DESC)"
    )

    # Apply RLS to both tables
    for table in [
        "civilization_reputation_history",
        "civilization_constitution_history",
    ]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """)


def downgrade() -> None:
    for table in [
        "civilization_constitution_history",
        "civilization_reputation_history",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
