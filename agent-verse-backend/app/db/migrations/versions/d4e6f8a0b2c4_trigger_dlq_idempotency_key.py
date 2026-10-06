"""trigger_dlq.idempotency_key: a DLQ retry replays the ORIGINAL firing (B2-OPEN-2).

A dead-lettered firing (rate limited, bulkhead full, gate unavailable, goal
enqueue failed) was retried with its own per-attempt key
(``dlq-retry:<id>:<n>``), so retrying a throttled delivery that the sender had
also redelivered successfully created a second goal. The DLQ row now records
the dedup key the firing was dispatched under, and ``POST /triggers/dlq/{id}/retry``
dispatches under that key: the replay and the redelivery dedupe against each
other. Rows written before this column have NULL and keep the old retry key.

``workflow_webhook_events.idempotency_key`` does the same for the workflow
webhook DLQ: the retry task runs a dead-lettered ``/wf-hooks`` delivery under
the delivery's live dedup key (``webhook:<delivery id>``), so a retry and a
sender redelivery start one run.

Revision ID: d4e6f8a0b2c4
Revises: b8d0f2a4c6e7
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4e6f8a0b2c4"
down_revision: str | Sequence[str] | None = "b8d0f2a4c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE trigger_dlq ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
    op.execute(
        "ALTER TABLE workflow_webhook_events ADD COLUMN IF NOT EXISTS idempotency_key TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workflow_webhook_events DROP COLUMN IF EXISTS idempotency_key")
    op.execute("ALTER TABLE trigger_dlq DROP COLUMN IF EXISTS idempotency_key")
