"""Create artifacts table for RPA and agent output persistence."""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("goal_id", sa.String(64), nullable=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "artifact_type",
            sa.String(50),
            nullable=False,
            server_default="file",
        ),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column(
            "content_type",
            sa.String(100),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column(
            "size_bytes", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])
    op.create_index("ix_artifacts_goal_id", "artifacts", ["goal_id"])
    op.create_index(
        "ix_artifacts_tenant_goal", "artifacts", ["tenant_id", "goal_id"]
    )
    op.create_index("ix_artifacts_created", "artifacts", ["created_at"])
    op.execute("ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY artifacts_tenant_isolation ON artifacts
        USING (tenant_id = current_setting('app.tenant_id', true))"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS artifacts_tenant_isolation ON artifacts")
    op.drop_table("artifacts")
