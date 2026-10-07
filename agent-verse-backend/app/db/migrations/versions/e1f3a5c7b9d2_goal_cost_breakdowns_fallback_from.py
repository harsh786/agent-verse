"""goal_cost_breakdowns.fallback_from: provenance of a failed-over role call.

The per-role breakdown (``GET /agent-runtime/traces`` role_calls, cost
metrics) attributed an executor call to the model the router REQUESTED, so a
goal whose pinned model was dead and failed over to the next one recorded the
dead model as having served it. The row's ``model`` is now the model that
actually served the call; ``fallback_from`` lists the distinct models the
role's calls failed over from first (``[]`` when the first model answered).

Additive only: a constant default (no table rewrite on Postgres 11+).

Revision ID: e1f3a5c7b9d2
Revises: d8f0b2c4e6a9
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e1f3a5c7b9d2"
down_revision: str | Sequence[str] | None = "d8f0b2c4e6a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s';")
    op.execute(
        "ALTER TABLE goal_cost_breakdowns "
        "ADD COLUMN IF NOT EXISTS fallback_from JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE goal_cost_breakdowns DROP COLUMN IF EXISTS fallback_from")
