"""Add SOC2-required fields to audit_log."""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("ip_address", sa.String(45), nullable=True))
    op.add_column("audit_log", sa.Column("user_agent", sa.String(500), nullable=True))
    op.add_column("audit_log", sa.Column("api_key_id", sa.String(64), nullable=True))
    op.add_column("audit_log", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("audit_log", sa.Column("connector_id", sa.String(64), nullable=True))
    op.create_index("ix_audit_log_api_key_id", "audit_log", ["api_key_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_api_key_id", table_name="audit_log")
    for col in ["ip_address", "user_agent", "api_key_id", "request_id", "connector_id"]:
        op.drop_column("audit_log", col)
