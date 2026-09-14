"""org_brain_decisions.guardrail_trace — persist the full 8-check guardrail trace.

Previously only the final ``Verdict(action, reason)`` from
``app.org.brain_guardrails.evaluate_guardrails`` was persisted. The Situation
Room's Brain Feed v2 needs to show WHICH of the 8 SENSE→DECIDE→GUARD→ACT
checks stopped a decision (and its number/limit), so this adds a nullable
JSONB column holding the ordered per-check trace
(``[{name, passed, detail, value, limit}, ...]``), written by
``BrainDecisionStore.record`` and returned by ``BrainDecisionStore.list``.

Revision ID: 0132_org_brain_guardrail_trace
Revises: 0131_merge_heads
"""

from __future__ import annotations

from alembic import op

revision = "0132_org_brain_guardrail_trace"
down_revision = "0131_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE org_brain_decisions ADD COLUMN IF NOT EXISTS guardrail_trace JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE org_brain_decisions DROP COLUMN IF EXISTS guardrail_trace")
