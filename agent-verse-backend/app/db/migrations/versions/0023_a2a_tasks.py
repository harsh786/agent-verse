"""A2A task persistence table."""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"


def upgrade() -> None:
    op.create_table(
        "a2a_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("goal_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("callback_url", sa.String(500), nullable=True),
        sa.Column("requester_id", sa.String(255), nullable=True),
        sa.Column("hmac_signature", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_a2a_tasks_tenant", "a2a_tasks", ["tenant_id"])
    op.execute("ALTER TABLE a2a_tasks ENABLE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY a2a_tasks_rls ON a2a_tasks
        USING (tenant_id = current_setting('app.tenant_id', true))""")


def downgrade() -> None:
    op.drop_table("a2a_tasks")
