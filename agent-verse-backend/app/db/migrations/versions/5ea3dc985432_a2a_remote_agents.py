"""a2a_remote_agents: the remote A2A agent registry, server-side and tenant-scoped.

Remote agents registered on the A2A page lived only in the browser's localStorage,
so other operators and the backend never saw them. ``app/api/a2a_remote_agents.py``
now stores them here; the server fetches and validates each agent card (SSRF
guard) on register/ping.

Tenant-isolated by FORCE'd RLS using ``app_current_tenant_uuid()`` (``c9d0e1f2a3b4``).

Revision ID: 5ea3dc985432
Revises: cf87de8eae52
"""

from __future__ import annotations

from alembic import op

revision = "5ea3dc985432"
down_revision = "cf87de8eae52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS a2a_remote_agents (
            id              VARCHAR(64) PRIMARY KEY,
            tenant_id       UUID NOT NULL,
            name            VARCHAR(200) NOT NULL,
            url             VARCHAR(2000) NOT NULL,
            card            JSONB NOT NULL,
            last_error      TEXT,
            last_checked_at TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_a2a_remote_agents_tenant_url UNIQUE (tenant_id, url)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_a2a_remote_agents_tenant_created "
        "ON a2a_remote_agents (tenant_id, created_at DESC)"
    )
    op.execute("ALTER TABLE a2a_remote_agents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE a2a_remote_agents FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS a2a_remote_agents_tenant_isolation ON a2a_remote_agents")
    op.execute(
        "CREATE POLICY a2a_remote_agents_tenant_isolation ON a2a_remote_agents "
        "USING (tenant_id = app_current_tenant_uuid()) "
        "WITH CHECK (tenant_id = app_current_tenant_uuid())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS a2a_remote_agents CASCADE")
