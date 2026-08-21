"""Add verifier_calibration table for tracking verifier verdicts vs outcomes.

Revision ID: 0073
Revises: 0072
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verifier_calibration",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=False),
        sa.Column("verifier_verdict", sa.Boolean, nullable=False),  # what verifier said
        sa.Column("actual_outcome", sa.Boolean, nullable=True),  # human/eval outcome
        sa.Column("verifier_model", sa.String(128), nullable=True),
        sa.Column("goal_text", sa.Text, nullable=True),
        sa.Column("iteration", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_verifier_calibration_tenant", "verifier_calibration", ["tenant_id"])
    op.create_index("ix_verifier_calibration_goal", "verifier_calibration", ["goal_id"])

    # RLS
    op.execute("ALTER TABLE verifier_calibration ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE verifier_calibration FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON verifier_calibration
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON verifier_calibration")
    op.drop_table("verifier_calibration")
