"""Add usage_records and billing_subscriptions tables.

Revision ID: 0077
Revises: 0076
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Usage records — emitted by CostController and goal completion
    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=True),
        sa.Column(
            "metric", sa.String(64), nullable=False
        ),  # goals | llm_tokens | tool_calls | storage_mb
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_usage_records_tenant", "usage_records", ["tenant_id"])
    op.create_index("ix_usage_records_period", "usage_records", ["period_start"])
    op.create_index("ix_usage_records_metric", "usage_records", ["metric"])

    # Billing subscriptions
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False, unique=True),
        sa.Column("stripe_customer_id", sa.String(128), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(128), nullable=True),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_billing_subscriptions_tenant", "billing_subscriptions", ["tenant_id"])

    # RLS on both
    for table in ["usage_records", "billing_subscriptions"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    for table in ["billing_subscriptions", "usage_records"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
