"""a2a_tasks_signature_unique: single-use A2A signatures across replicas without Redis.

Inbound A2A tasks are signed (``X-A2A-Timestamp`` + ``X-A2A-Signature``) and each
signature may be used once inside the ±5 min window. Without Redis the replay
cache was a per-process dict, so on a multi-replica deployment each replica
accepted a captured signature once (a02-F040-11). Every accepted task now
records its signature in ``a2a_tasks.hmac_signature`` (a column that existed but
was never written), and this unique index makes a second use of the same
signature fail the INSERT on any replica. Partial: unsigned dev tasks store NULL.
The column was never written, so there are no duplicates to clean up.

Revision ID: b3d5f7a9c1e3
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3d5f7a9c1e3"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_a2a_tasks_hmac_signature"


def upgrade() -> None:
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON a2a_tasks (hmac_signature) "
        "WHERE hmac_signature IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
