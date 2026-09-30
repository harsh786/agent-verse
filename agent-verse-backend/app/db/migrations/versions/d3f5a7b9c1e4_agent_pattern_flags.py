"""Agents: persist reasoning-pattern flags.

``enable_cot`` / ``enable_reflection`` / ``enable_goal_tree`` /
``enable_self_refine`` / ``enable_self_consistency`` / ``enable_tree_of_thoughts``
/ ``enable_peer_review`` / ``enable_supervisor`` / ``enable_debate`` had no
column, so the API could not set them and a goal's ``agent_pattern_flags``
snapshot was always empty — those graph nodes never ran outside the
rollout-gated v2 profile (CORE-04). One JSONB map keeps them together.

Revision ID: d3f5a7b9c1e4
Revises: c8d2f4a6b1e3
"""

from __future__ import annotations

from alembic import op

revision = "d3f5a7b9c1e4"
down_revision = "c8d2f4a6b1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS pattern_flags JSONB NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS pattern_flags")
