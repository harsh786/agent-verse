"""Create mcp_servers, mcp_credentials, and oauth_tokens tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── mcp_servers ───────────────────────────────────────────────────────
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("auth_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, nullable=True, server_default=""),
        sa.Column("priority", sa.Integer, nullable=True, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=True, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_mcp_servers_tenant_id", "mcp_servers", ["tenant_id"])

    op.execute("ALTER TABLE mcp_servers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_servers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mcp_servers_tenant_isolation ON mcp_servers "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── mcp_credentials (AES-256-GCM Fernet encrypted) ───────────────────
    op.create_table(
        "mcp_credentials",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "server_id",
            sa.String(32),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("encrypted_config", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_mcp_credentials_server_id", "mcp_credentials", ["server_id"])
    op.create_index("ix_mcp_credentials_tenant_id", "mcp_credentials", ["tenant_id"])

    op.execute("ALTER TABLE mcp_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_credentials FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mcp_credentials_tenant_isolation ON mcp_credentials "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── oauth_tokens (access + refresh tokens, both encrypted) ────────────
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "server_id",
            sa.String(32),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("access_token_enc", sa.Text, nullable=False),
        sa.Column("refresh_token_enc", sa.Text, nullable=True),
        sa.Column("token_type", sa.String(50), nullable=True, server_default="Bearer"),
        sa.Column("scope", sa.Text, nullable=True, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_oauth_tokens_server_id", "oauth_tokens", ["server_id"])
    op.create_index("ix_oauth_tokens_tenant_id", "oauth_tokens", ["tenant_id"])

    op.execute("ALTER TABLE oauth_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE oauth_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY oauth_tokens_tenant_isolation ON oauth_tokens "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS oauth_tokens_tenant_isolation ON oauth_tokens")
    op.drop_table("oauth_tokens")
    op.execute(
        "DROP POLICY IF EXISTS mcp_credentials_tenant_isolation ON mcp_credentials"
    )
    op.drop_table("mcp_credentials")
    op.execute("DROP POLICY IF EXISTS mcp_servers_tenant_isolation ON mcp_servers")
    op.drop_table("mcp_servers")
