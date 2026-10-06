"""goals: index the Agent Runtime trace id kept in execution_context (a10-F237-06)

``GET /agent-runtime/traces/{id}`` resolves a trace id the bounded LRU / Redis
store no longer has (7-day TTL, Redis outage, another replica) from the goal it
was created for (``execution_context ->> 'agent_runtime_trace_id'``). A partial
expression index keeps that lookup off the tenant's whole goal history.

Revision ID: a8d2f6c4e1b9
Revises: f5a9c3e7b2d4
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a8d2f6c4e1b9"
down_revision: str | Sequence[str] | None = "f5a9c3e7b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_goals_agent_runtime_trace_id "
            "ON goals (tenant_id, (execution_context ->> 'agent_runtime_trace_id')) "
            "WHERE (execution_context ->> 'agent_runtime_trace_id') IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_goals_agent_runtime_trace_id")
