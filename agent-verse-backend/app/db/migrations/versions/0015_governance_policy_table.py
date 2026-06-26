"""Add governance_policies table for DB-backed policy persistence."""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governance_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tools_pattern", sa.String(500), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("priority", sa.Integer(), default=0),
        sa.Column("description", sa.Text(), default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE governance_policies ENABLE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY governance_policies_tenant_isolation
        ON governance_policies USING (tenant_id = current_setting('app.tenant_id', true))""")


def downgrade() -> None:
    op.drop_table("governance_policies")
