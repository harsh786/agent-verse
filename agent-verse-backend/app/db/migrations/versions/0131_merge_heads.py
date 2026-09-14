"""merge heads — reconcile the org-brain/triggers and agent-memory/governance lineages.

Both lineages branched off 0127 and added independent tables (org-brain: 0128/0129
trigger-DLQ + org tables; governance/memory: 0128_agent_grants → 0129_audit_chain →
0130_prospective_memory). They touch disjoint tables, so this is a pure Alembic DAG
merge point — no schema changes.

Revision ID: 0131_merge_heads
Revises: 0129, 0130_prospective_memory
"""

from __future__ import annotations

revision = "0131_merge_heads"
down_revision = ("0129", "0130_prospective_memory")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
