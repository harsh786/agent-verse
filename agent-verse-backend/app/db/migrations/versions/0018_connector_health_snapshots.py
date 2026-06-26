"""Add connector_health_snapshots table."""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_health_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("server_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ch_snapshots_server_tenant", "connector_health_snapshots",
                    ["server_id", "tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_ch_snapshots_server_tenant", table_name="connector_health_snapshots")
    op.drop_table("connector_health_snapshots")
