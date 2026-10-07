"""goals: index the batch / builder-project ids kept in execution_context (a10-F231-01, F229-02)

``POST /goals/batch`` and ``POST /builder/projects`` hand out ids (a batch id, a
builder project id) that only live in each goal's ``execution_context``. Their
status routes resolve them with ``execution_context ->> '<key>' = :v`` under an
explicit tenant predicate (``GoalService.find_goals_by_context``). Partial
expression indexes keep that lookup off the tenant's whole goal history (only
goals that carry the key are indexed).

Revision ID: c4e8a2f6b1d3
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4e8a2f6b1d3"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYS = {"batch_id": "ix_goals_batch_id", "builder_project_id": "ix_goals_builder_project_id"}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for key, name in _KEYS.items():
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON goals (tenant_id, (execution_context ->> '{key}')) "
                f"WHERE (execution_context ->> '{key}') IS NOT NULL"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in _KEYS.values():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
