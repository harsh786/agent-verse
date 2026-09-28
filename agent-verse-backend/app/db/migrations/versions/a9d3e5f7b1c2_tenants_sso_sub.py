"""tenants.sso_sub: the Keycloak subject a JIT-provisioned tenant belongs to.

Keycloak JIT provisioning INSERTed ``tenants.plan`` (the column is
``plan_tier``) and wrote ``sso_sub`` inside ``suppress(Exception)`` although the
column did not exist, so every SSO first login failed to persist and the
``WHERE sso_sub = :sub`` lookup could never match — each login provisioned a
fresh in-memory ghost tenant.

``tenants`` carries no RLS (it is the tenancy root), so no policy is needed.

Revision ID: a9d3e5f7b1c2
Revises: f1a2b3c4d5e7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9d3e5f7b1c2"
down_revision = "f1a2b3c4d5e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("sso_sub", sa.String(255), nullable=True))
    op.create_index(
        "uq_tenants_sso_sub",
        "tenants",
        ["sso_sub"],
        unique=True,
        postgresql_where=sa.text("sso_sub IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_tenants_sso_sub", table_name="tenants")
    op.drop_column("tenants", "sso_sub")
