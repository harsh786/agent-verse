"""oauth_tokens: unique (tenant_id, server_id); drop the FK to mcp_servers.

OAuth token persistence never worked: the upsert used ON CONFLICT
(tenant_id, server_id) with no matching unique constraint, and server_id had a
foreign key to mcp_servers — a table the connector registry (Redis) never
writes — so every insert was rejected and every token was lost on restart.

Revision ID: f1a2b3c4d5e7
Revises: c7e1a9d4b3f2
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "f1a2b3c4d5e7"
down_revision = "c7e1a9d4b3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for (name,) in bind.execute(
        text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'oauth_tokens'::regclass "
            "AND contype = 'f'"
        )
    ).fetchall():
        op.execute(f'ALTER TABLE oauth_tokens DROP CONSTRAINT "{name}"')
    # Keep only the newest row per (tenant, server) before adding the key.
    op.execute(
        "DELETE FROM oauth_tokens a USING oauth_tokens b "
        "WHERE a.tenant_id = b.tenant_id AND a.server_id = b.server_id "
        "AND a.created_at < b.created_at"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_oauth_tokens_tenant_server "
        "ON oauth_tokens (tenant_id, server_id)"
    )
    # WITH CHECK so a write can never target another tenant.
    op.execute("DROP POLICY IF EXISTS oauth_tokens_tenant_isolation ON oauth_tokens")
    op.execute(
        "CREATE POLICY oauth_tokens_tenant_isolation ON oauth_tokens "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_oauth_tokens_tenant_server")
