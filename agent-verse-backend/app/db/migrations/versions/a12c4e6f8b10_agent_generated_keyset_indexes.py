"""Keyset indexes for agent-generated knowledge Sources (P1e-1)

An ``agent_generated`` Source reads the tenant's completed goals, decided
approvals, decided workflow approval gates and completed workflow runs in
``(timestamp, id)`` order after its cursor. These partial indexes keep each page
a bounded index range scan whatever the size of the tables (millions of goals /
runs per tenant). Built CONCURRENTLY (no write lock).

Revision ID: a12c4e6f8b10
Revises: e7b1c4d9a2f6
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a12c4e6f8b10"
down_revision: str | None = "e7b1c4d9a2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "ix_goals_tenant_completed_at_keyset",
        "goals (tenant_id, completed_at, id) "
        "WHERE status IN ('complete', 'completed') AND completed_at IS NOT NULL",
    ),
    (
        "ix_approval_requests_tenant_resolved_keyset",
        "approval_requests (tenant_id, resolved_at, id) "
        "WHERE status IN ('approved', 'rejected') AND resolved_at IS NOT NULL",
    ),
    (
        "ix_workflow_approvals_tenant_decided_keyset",
        "workflow_approvals (tenant_id, updated_at, request_id) "
        "WHERE status IN ('approved', 'rejected', 'decided')",
    ),
    (
        "ix_workflow_runs_tenant_completed_keyset",
        "workflow_runs (tenant_id, completed_at, id) "
        "WHERE status = 'complete' AND completed_at IS NOT NULL",
    ),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, target in _INDEXES:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {target}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _target in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
