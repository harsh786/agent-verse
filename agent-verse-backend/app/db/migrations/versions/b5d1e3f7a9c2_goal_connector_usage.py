"""goal_connector_usage: which goals used which connector (MCPREG-03).

GET /connectors/{id}/usage ran an unindexed ``LIKE '%id%'`` over
``goals.execution_context->>'connector_ids'`` — a key nothing ever wrote, so it
always answered zero; and a substring would have matched other connections
("builtin-github" inside "builtin-github:work-org"). One row per (tenant,
connector, goal), written when a goal's tool call reaches the connector, read
by an exact, indexed lookup.

Revision ID: b5d1e3f7a9c2
Revises: a7c4e2f9d1b3
"""

from __future__ import annotations

from alembic import op

revision = "b5d1e3f7a9c2"
down_revision = "a7c4e2f9d1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_connector_usage (
            tenant_id      VARCHAR(64)  NOT NULL,
            connector_id   VARCHAR(255) NOT NULL,
            goal_id        VARCHAR(64)  NOT NULL,
            first_used_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, connector_id, goal_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_goal_connector_usage_lookup "
        "ON goal_connector_usage (tenant_id, connector_id, first_used_at DESC)"
    )
    op.execute("ALTER TABLE goal_connector_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_connector_usage FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS goal_connector_usage_tenant_isolation ON goal_connector_usage"
    )
    op.execute(
        "CREATE POLICY goal_connector_usage_tenant_isolation ON goal_connector_usage "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_connector_usage")
