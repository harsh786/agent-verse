"""RLS (ENABLE + FORCE) on the marketplace monetization tenant tables.

``marketplace_author_accounts`` (tenant_id) and ``marketplace_purchases``
(buyer_tenant_id) were created in 0079 without row-level security, so any
query that reached them saw every tenant's Stripe accounts and purchases.

Revision ID: a9c4e2f7b1d3
Revises: a9d3e5f7b1c2
"""

from __future__ import annotations

from alembic import op

revision = "a9c4e2f7b1d3"
down_revision = "a9d3e5f7b1c2"
branch_labels = None
depends_on = None

_TABLES = (
    ("marketplace_author_accounts", "tenant_id"),
    ("marketplace_purchases", "buyer_tenant_id"),
)


def upgrade() -> None:
    for table, column in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({column} = current_setting('app.tenant_id', TRUE)) "
            f"WITH CHECK ({column} = current_setting('app.tenant_id', TRUE))"
        )


def downgrade() -> None:
    for table, _column in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
