"""0113 — durable guardrail_rules table with tenant RLS (P1-4).

Guardrail rules previously lived only in ``GuardrailsEngine._rules`` (in-memory)
and were lost on restart. This table persists them, tenant-scoped and protected
by Row-Level Security exactly like the other tenant tables (see 0106).

Revision ID: 0113
Revises: 0112
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guardrail_rules",
        sa.Column("rule_id", sa.String(200), primary_key=True),
        sa.Column("tenant_id", sa.String(200), nullable=False, index=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("rule_type", sa.String(50), nullable=False),
        sa.Column("layers", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("action", sa.String(50), nullable=False, server_default="block"),
        sa.Column("categories", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("severity", sa.String(20), nullable=False, server_default="high"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_guardrail_rules_tenant",
        "guardrail_rules",
        ["tenant_id"],
    )
    # Row-Level Security — mirror the 0106 pattern (ENABLE + FORCE + tenant policy)
    # so the migration/owner role cannot accidentally bypass isolation either.
    op.execute("ALTER TABLE guardrail_rules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guardrail_rules FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY guardrail_rules_tenant_isolation ON guardrail_rules "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS guardrail_rules_tenant_isolation ON guardrail_rules")
    op.drop_table("guardrail_rules")
