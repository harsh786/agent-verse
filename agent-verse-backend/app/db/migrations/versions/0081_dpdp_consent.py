"""India DPDP consent tables."""

import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dpdp_consents",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, index=True),
        sa.Column("data_principal_id", sa.String, nullable=False),
        sa.Column("purpose", sa.String, nullable=False),
        sa.Column("consent_given", sa.Boolean, nullable=False),
        sa.Column("consent_timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String, nullable=True),
    )
    op.create_table(
        "dpdp_erasure_requests",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, index=True),
        sa.Column("data_principal_id", sa.String, nullable=False),
        sa.Column("status", sa.String, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grievance_officer_notified", sa.Boolean, server_default="false"),
    )
    for tbl in ("dpdp_consents", "dpdp_erasure_requests"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {tbl} USING (tenant_id = current_setting('app.tenant_id', TRUE)) WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
        )


def downgrade() -> None:
    op.drop_table("dpdp_erasure_requests")
    op.drop_table("dpdp_consents")
