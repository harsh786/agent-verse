"""Eval-suite runs record the agent and agent config they exercised (MEM-52).

Golden goals were submitted with no agent_id (auto-routed) and a run recorded
no agent, so the rollout gate promoted an agent on a run that may never have
executed it, after any later edit of its prompt/model/tools. A run now records
``agent_id``, ``agent_config_hash`` (prompt, model, tools, policies, limits,
pattern flags) and ``agent_version``; the gate selects the latest completed run
for (suite, agent) whose hash and dataset version are current.

Revision ID: e7a1c4d2b9f1
Revises: e7a1c4d2b9f0
"""

from __future__ import annotations

from alembic import op

revision = "e7a1c4d2b9f1"
down_revision = "e7a1c4d2b9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eval_suite_results "
        "ADD COLUMN IF NOT EXISTS agent_id VARCHAR(32), "
        "ADD COLUMN IF NOT EXISTS agent_config_hash VARCHAR(64), "
        "ADD COLUMN IF NOT EXISTS agent_version INTEGER"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eval_suite_results_tenant_suite_agent "
        "ON eval_suite_results (tenant_id, suite_id, agent_id, run_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_eval_suite_results_tenant_suite_agent")
    op.execute(
        "ALTER TABLE eval_suite_results DROP COLUMN IF EXISTS agent_version, "
        "DROP COLUMN IF EXISTS agent_config_hash, DROP COLUMN IF EXISTS agent_id"
    )
