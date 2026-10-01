"""auction_registry: per-auction key registry for public sealed bids (AUCTION-KEYS)

Bids submitted through ``POST .../auction/bids`` were opaque ciphertext with no key
anyone held, so they could be counted but never opened or scored. Each registry
row is one sealed-bid auction: its public announcement and X25519 public key, and
``sealed_keys`` — the private key and bidder signing secrets as vault ciphertext
bound to (tenant, auction). FORCE ROW LEVEL SECURITY isolates tenants.

Revision ID: b4d81c6e2a90
Revises: 2078a13e6728
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4d81c6e2a90"
down_revision: str | None = "2078a13e6728"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auction_registry",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("announcement", postgresql.JSONB(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sealed_keys", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "session_id", "idempotency_key", name="uq_auction_registry_key"
        ),
        sa.CheckConstraint("state IN ('open', 'closed')", name="ck_auction_registry_state"),
    )
    op.create_index(
        "ix_auction_registry_tenant_session", "auction_registry", ["tenant_id", "session_id"]
    )
    op.execute("ALTER TABLE auction_registry ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auction_registry FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY auction_registry_tenant_isolation ON auction_registry "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS auction_registry_tenant_isolation ON auction_registry")
    op.drop_index("ix_auction_registry_tenant_session", table_name="auction_registry")
    op.drop_table("auction_registry")
