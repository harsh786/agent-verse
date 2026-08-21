"""GST invoices table for 7-year retention."""

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gst_invoices",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, index=True),
        sa.Column("invoice_number", sa.String, nullable=False, unique=True),
        sa.Column("invoice_date", sa.String, nullable=False),
        sa.Column("buyer_name", sa.String, nullable=False),
        sa.Column("buyer_gstin", sa.String, nullable=True),
        sa.Column("taxable_amount_inr", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_gst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_amount_inr", sa.Numeric(12, 2), nullable=False),
        sa.Column("invoice_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE gst_invoices ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE gst_invoices FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON gst_invoices "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.drop_table("gst_invoices")
