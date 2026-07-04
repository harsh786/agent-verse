"""Add users and tenant_memberships tables for SaaS identity.

Revision ID: 0072
Revises: 0071
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users table (global identity, email-unique)
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("picture_url", sa.String(1024), nullable=True),
        sa.Column("google_sub", sa.String(255), nullable=True, unique=True),
        sa.Column("keycloak_sub", sa.String(255), nullable=True, unique=True),
        sa.Column("mfa_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_google_sub", "users", ["google_sub"])

    # Tenant memberships (user ↔ tenant with role)
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(64), nullable=False, server_default="viewer"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("invited_by", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
    )
    op.create_index("ix_tenant_memberships_tenant", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user", "tenant_memberships", ["user_id"])

    # No RLS on users (global table) — RLS on tenant_memberships
    op.execute("ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON tenant_memberships
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_table("users")
