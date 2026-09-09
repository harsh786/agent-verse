"""Widen ``approval_requests.goal_id`` from VARCHAR(32) to VARCHAR(64).

``approval_requests.goal_id`` was created as ``String(32)`` (migration 0005),
which fits a 32-char ``uuid.hex`` goal id but NOT a 36-char dashed UUID. The AI
Org mission-HITL path (``app/org/service.py``) creates approval gates whose
``goal_id`` is the mission/task UUID (36 chars), so the best-effort DB persist in
``HITLGateway._db_persist_approval_request`` failed with
``StringDataRightTruncationError`` (swallowed as a warning) — org HITL approvals
were never durably persisted, and could be lost across restarts/replicas or
missed by a DB-backed ``list_pending``. Widen the column so any goal-id shape
persists. Idempotent (``ALTER … TYPE`` is a no-op if already wide enough).

Revision ID: 0118
Revises: 0117
"""

from __future__ import annotations

from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approval_requests ALTER COLUMN goal_id TYPE VARCHAR(64)")


def downgrade() -> None:
    # Truncating back to 32 could fail on existing 36-char UUIDs; guard the shrink.
    op.execute("ALTER TABLE approval_requests ALTER COLUMN goal_id TYPE VARCHAR(32) USING left(goal_id, 32)")
