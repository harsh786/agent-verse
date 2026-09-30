"""channel_tenant_mappings: ownership proof (TRG-03)

``POST /channels/mappings`` was first-come: any tenant could claim another
tenant's Slack workspace, Discord guild, number or email address and receive its
inbound events. A mapping now carries a ``status``:

* ``pending_verification`` — new claims; routes nothing. Carries a one-time code
  (SHA-256 hash only) and its expiry. Several tenants may hold pending claims for
  the same channel, so nobody can squat on a channel they do not control.
* ``verified`` — an inbound message on the channel carried the code.
* ``legacy_unverified`` — every mapping that existed before this revision. It
  keeps routing (no breakage) and is surfaced for verification in the UI.

At most one ROUTABLE (verified / legacy) mapping may exist per channel; a tenant
holds at most one mapping per channel.

Revision ID: d4e9a1c7b3f2
Revises: 61e5532b6fb3
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e9a1c7b3f2"
down_revision: str | None = "61e5532b6fb3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "channel_tenant_mappings"


def upgrade() -> None:
    # Backfill: pre-existing rows become legacy_unverified (they keep routing);
    # the default then flips so every NEW row starts pending.
    op.add_column(
        _TABLE,
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="legacy_unverified"
        ),
    )
    op.alter_column(_TABLE, "status", server_default="pending_verification")
    op.add_column(_TABLE, sa.Column("verification_code_hash", sa.String(64), nullable=True))
    op.add_column(
        _TABLE, sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_channel_tenant_mappings_status",
        _TABLE,
        "status IN ('pending_verification', 'verified', 'legacy_unverified')",
    )

    op.execute("DROP INDEX IF EXISTS ix_channel_tenant_channel_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_channel_tenant_channel_routable_unique "
        f"ON {_TABLE} (channel_type, channel_id) "
        "WHERE channel_id <> '' AND status IN ('verified', 'legacy_unverified')"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_channel_tenant_tenant_channel_unique "
        f"ON {_TABLE} (tenant_id, channel_type, channel_id) WHERE channel_id <> ''"
    )


def downgrade() -> None:
    # Pending claims never routed; dropping them restores the one-row-per-channel
    # invariant the previous unique index needs.
    op.execute(f"DELETE FROM {_TABLE} WHERE status = 'pending_verification'")
    op.execute("DROP INDEX IF EXISTS ix_channel_tenant_tenant_channel_unique")
    op.execute("DROP INDEX IF EXISTS ix_channel_tenant_channel_routable_unique")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_channel_tenant_channel_unique "
        f"ON {_TABLE} (channel_type, channel_id) WHERE channel_id <> ''"
    )
    op.drop_constraint("ck_channel_tenant_mappings_status", _TABLE, type_="check")
    op.drop_column(_TABLE, "verified_at")
    op.drop_column(_TABLE, "verification_expires_at")
    op.drop_column(_TABLE, "verification_code_hash")
    op.drop_column(_TABLE, "status")
