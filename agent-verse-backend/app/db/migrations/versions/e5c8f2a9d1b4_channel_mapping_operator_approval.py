"""channel_tenant_mappings: operator approval + superseded (TRG-03 follow-up)

A one-time code received on an SMS number or email address (or a public form, a
meeting account, a voice number) only proves someone can SEND there, not that
they own it. Claims on those channels are now ``pending_operator_approval``: they
route nothing and cannot be verified by code; a platform operator approves
(``verified``) or rejects (``rejected``) them. A legacy mapping displaced by
another tenant's proof is kept as ``superseded`` (no routing) instead of being
deleted.

Upgrade moves every still-pending send-only claim into the operator queue and
voids its outstanding code.

Revision ID: e5c8f2a9d1b4
Revises: d4e9a1c7b3f2
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5c8f2a9d1b4"
down_revision: str | None = "d4e9a1c7b3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "channel_tenant_mappings"
_CHECK = "ck_channel_tenant_mappings_status"
_SEND_ONLY = "('sms', 'email', 'form', 'meeting', 'voice')"


def upgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "status IN ('pending_verification', 'verified', 'legacy_unverified', "
        "'pending_operator_approval', 'superseded', 'rejected')",
    )
    op.execute(
        f"UPDATE {_TABLE} SET status = 'pending_operator_approval', "
        "verification_code_hash = NULL, verification_expires_at = NULL "
        f"WHERE status = 'pending_verification' AND lower(channel_type) IN {_SEND_ONLY}"
    )


def downgrade() -> None:
    # Superseded / rejected rows never route (the previous revision deleted such
    # rivals); operator-queue claims become plain pending claims (non-routing).
    op.execute(f"DELETE FROM {_TABLE} WHERE status IN ('superseded', 'rejected')")
    op.execute(
        f"UPDATE {_TABLE} SET status = 'pending_verification' "
        "WHERE status = 'pending_operator_approval'"
    )
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "status IN ('pending_verification', 'verified', 'legacy_unverified')",
    )
