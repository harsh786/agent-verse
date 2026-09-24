"""Add DEFAULT partitions to cost_ledger, audit_events, policy_evaluations.

Distributed-scale audit (partitioning readiness): 0058_cost_optimization,
0057_audit_rails_v2, and 0056_governance_v2 each created their table with
``PARTITION BY RANGE (created_at)`` and then pre-created only 24 monthly
partitions (2026-01 through 2027-12) — with NO ``DEFAULT`` partition.

A range-partitioned table with no DEFAULT partition and no partition covering
a given value rejects the INSERT outright:
    ERROR: no partition of relation "cost_ledger" found for row

So from 2028-01-01 onward, every insert into any of these three tables would
start hard-failing across every replica simultaneously — cost tracking
(LLM billing/budget enforcement), the audit WAL drain, and policy-evaluation
logging would all break at once, with no code change required to trigger it.
(``guardrail_violations`` in 0055_guardrails already got this right — it has
a ``guardrail_violations_default`` partition. This migration brings the other
three RANGE-partitioned tables up to the same standard.)

A DEFAULT partition is the safety net, not a substitute for ongoing partition
provisioning: rows that land in it are not covered by the monthly partitions'
pruning/retention story and a full table scan of the default partition scales
with however much ends up there. Provisioning fresh monthly partitions ahead
of time (see the new ``ensure_future_partitions`` maintenance task) should
keep it empty in the steady state; this migration only guarantees inserts
never hard-fail if that maintenance task is ever delayed or fails.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3f2bbce84e68"
down_revision: str | None = "06f8d39de7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("cost_ledger", "audit_events", "policy_evaluations")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {table}_default "
            f"PARTITION OF {table} DEFAULT"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}_default")
