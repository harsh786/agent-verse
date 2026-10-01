"""agents: GIN full-text index for the auto-routing candidate pre-filter (CORE-33).

POST /goals auto-routing read a tenant's whole agents table per submission and
the router kept only the 50 newest, so large tenants paid a full load and older
agents were never candidates. ``AgentStore.routing_candidates`` now matches the
goal against this expression (``app/api/agents._ROUTING_TSV_SQL`` — must stay
identical) with ``LIMIT``, so the pre-filter is index-backed.

Revision ID: b9d4f2a6c8e1
Revises: a7c3e9f1b5d2
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b9d4f2a6c8e1"
down_revision: str | None = "a7c3e9f1b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agents_routing_fts ON agents USING gin ("
        "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(goal_template, '') "
        "|| ' ' || coalesce(connector_ids::text, '')))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agents_routing_fts")
