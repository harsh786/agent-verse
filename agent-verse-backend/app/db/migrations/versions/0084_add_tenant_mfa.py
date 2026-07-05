"""Add tenant_mfa table for persistent MFA storage.

Revision ID: 0084_add_tenant_mfa
Revises: 0083_gst_invoices
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084_add_tenant_mfa"
down_revision: str | None = "0083_gst_invoices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_mfa",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_codes_hashed", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_mfa_tenant_id"),
    )
    op.create_index("ix_tenant_mfa_tenant_id", "tenant_mfa", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_mfa_tenant_id", table_name="tenant_mfa")
    op.drop_table("tenant_mfa")
