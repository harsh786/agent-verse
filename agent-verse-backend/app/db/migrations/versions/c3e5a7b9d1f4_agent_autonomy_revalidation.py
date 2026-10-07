"""agent_autonomy_revalidation: a fully-autonomous agent's config change re-tests it.

Owner decision on a05-F095-04: a behaviour-config change to a
``fully-autonomous`` agent (PUT /agents/{id}, a self-optimizer apply or
rollback) is accepted, the agent is demoted to ``bounded-autonomous`` and its
rollout-gate eval suite runs against the new config; when that run passes the
gate the agent is promoted back. ``agents.autonomy_revalidation`` holds that
state (``app/intelligence/autonomy_revalidation.py``): the pending marker
(``state``, ``token``, ``run_id``, ``eval_suite_id``, ``agent_config_hash``,
...) and, once resolved, the outcome. NULL = never re-validated.

The partial index serves the reconciliation sweep (pending markers only).

Revision ID: c3e5a7b9d1f4
Revises: a7c9e1f3b5d7
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3e5a7b9d1f4"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS autonomy_revalidation JSONB")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agents_autonomy_revalidation_pending "
        "ON agents (tenant_id, id) "
        "WHERE (autonomy_revalidation ->> 'state') = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agents_autonomy_revalidation_pending")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS autonomy_revalidation")
