"""user_sessions: Postgres-authoritative browser SSO sessions (SAML-01).

A SAML (or Google) login ends in a session row; the browser exchanges a one-time
login code for an opaque ``avs_…`` bearer token. Only SHA-256 digests are stored.

RLS: FORCE, tenant isolation on ``tenant_id``. Authentication runs before any
tenant context exists, so — like ``api_keys_by_presented_hash`` — a SELECT-only
permissive policy makes exactly the row whose token / login-code digest the
caller presents (``app.session_token_hash`` / ``app.session_code_hash``)
visible. Knowing a token's SHA-256 is equivalent to holding the token; an empty
GUC matches nothing. Writes always run under the tenant GUC.

Revision ID: a7c3e9f1b2d4
Revises: e2e2811317df
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c3e9f1b2d4"
down_revision = "e2e2811317df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("login_code_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("login_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_method", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_sessions_tenant_user", "user_sessions", ["tenant_id", "user_id"])
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    # Retention sweep (prune_user_sessions) walks expired rows in batches.
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.execute("ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON user_sessions
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """
    )
    op.execute(
        """
        CREATE POLICY user_sessions_by_presented_hash ON user_sessions
            AS PERMISSIVE FOR SELECT
            USING (
                (
                    COALESCE(current_setting('app.session_token_hash', true), '') <> ''
                    AND token_hash = current_setting('app.session_token_hash', true)
                )
                OR (
                    COALESCE(current_setting('app.session_code_hash', true), '') <> ''
                    AND login_code_hash = current_setting('app.session_code_hash', true)
                )
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_sessions_by_presented_hash ON user_sessions")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON user_sessions")
    op.drop_table("user_sessions")
