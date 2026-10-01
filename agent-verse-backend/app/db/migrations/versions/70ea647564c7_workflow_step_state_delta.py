"""Persist each workflow step's state delta with its result (WF-34).

A resumed run (approval, pause, crash re-dispatch) rebuilds its state from the
definition and skips steps already COMPLETE. It restored only their outputs, so
variables a step set (``vars``), foreach progress and the step's cost/tokens
were lost: post-resume steps read defaults and the run row's cost covered only
the resumed segment. ``state_delta`` holds the non-output state a step returned
so a skipped step replays it.

Revision ID: 70ea647564c7
Revises: cf87de8eae52
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "70ea647564c7"
down_revision: str | None = "cf87de8eae52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE workflow_step_results ADD COLUMN IF NOT EXISTS state_delta JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE workflow_step_results DROP COLUMN IF EXISTS state_delta")
