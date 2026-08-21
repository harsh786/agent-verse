"""Goal feedback for RLHF-lite loop."""

import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_feedback",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("goal_id", sa.String, nullable=False, index=True),
        sa.Column("tenant_id", sa.String, nullable=False, index=True),
        sa.Column("rating", sa.SmallInteger, nullable=False),
        sa.Column("correction", sa.Text, nullable=True),
        sa.Column("step_id", sa.String, nullable=True),
        sa.Column("promoted_to_golden", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE goal_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON goal_feedback USING (tenant_id = current_setting('app.tenant_id', TRUE)) WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.drop_table("goal_feedback")
