"""agent_snapshots: one row per (tenant_id, agent_id, version) (a10-F236-04)

``POST /agents/{id}/snapshot`` numbered a snapshot ``len(existing) + 1`` from a
separate read, so concurrent snapshots (or a read that failed and returned [])
produced duplicate versions. The version is now allocated in the INSERT and this
unique constraint turns a race into a retried conflict.

Existing duplicates are renumbered first: the earliest row of each duplicate
group keeps its number, later ones move past the agent's current maximum (in
``snapshotted_at`` order), and the ``version`` inside the snapshot JSON follows.
The table is FORCE RLS, so the owner lifts FORCE for the data fix inside this
transaction and restores it.

Revision ID: e2b6d4f8a1c3
Revises: c4e8a2f6b1d3
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e2b6d4f8a1c3"
down_revision: str | Sequence[str] | None = "c4e8a2f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_agent_snapshots_tenant_agent_version"


def upgrade() -> None:
    op.execute("ALTER TABLE agent_snapshots NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        WITH ranked AS (
            SELECT id, tenant_id, agent_id, snapshotted_at,
                   row_number() OVER (
                       PARTITION BY tenant_id, agent_id, version
                       ORDER BY snapshotted_at, id
                   ) AS dup_rank
            FROM agent_snapshots
        ),
        maxes AS (
            SELECT tenant_id, agent_id, MAX(version) AS mv
            FROM agent_snapshots
            GROUP BY tenant_id, agent_id
        ),
        renumbered AS (
            SELECT r.id,
                   m.mv + row_number() OVER (
                       PARTITION BY r.tenant_id, r.agent_id
                       ORDER BY r.snapshotted_at, r.id
                   ) AS nv
            FROM ranked r
            JOIN maxes m ON m.tenant_id = r.tenant_id AND m.agent_id = r.agent_id
            WHERE r.dup_rank > 1
        )
        UPDATE agent_snapshots a
        SET version = renumbered.nv,
            snapshot = jsonb_set(a.snapshot, '{version}', to_jsonb(renumbered.nv))
        FROM renumbered
        WHERE a.id = renumbered.id
        """
    )
    op.execute("ALTER TABLE agent_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(
        f"ALTER TABLE agent_snapshots ADD CONSTRAINT {_CONSTRAINT} "
        "UNIQUE (tenant_id, agent_id, version)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE agent_snapshots DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
