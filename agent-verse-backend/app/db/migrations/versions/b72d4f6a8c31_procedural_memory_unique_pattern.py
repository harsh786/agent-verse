"""procedural_memories: one row per (tenant, goal pattern) + trigram index (MEM-11)

``learn`` inserted a new row on every cache miss (``ON CONFLICT DO NOTHING``
never conflicted on the random id), so a tenant accumulated duplicate rows per
pattern whose ``use_count`` stayed 1. Duplicates are merged (use counts summed,
success rates use-weighted) and a unique ``(tenant_id, goal_pattern)`` index
lets ``learn`` upsert. A GIN trigram index backs relevance-ranked recall.

Revision ID: b72d4f6a8c31
Revises: a91c3e5f7b20
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b72d4f6a8c31"
down_revision: str | None = "a91c3e5f7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "procedural_memories"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # FORCE RLS hides every row from the migration role (no tenant GUC).
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        WITH agg AS (
            SELECT tenant_id, goal_pattern,
                   (array_agg(id ORDER BY last_used_at DESC NULLS LAST, id))[1] AS keep_id,
                   SUM(use_count) AS uses,
                   SUM(success_rate * use_count) / NULLIF(SUM(use_count), 0) AS rate,
                   MAX(last_used_at) AS last_used
              FROM {_TABLE}
             GROUP BY tenant_id, goal_pattern
            HAVING COUNT(*) > 1
        )
        UPDATE {_TABLE} p
           SET use_count = agg.uses,
               success_rate = COALESCE(agg.rate, p.success_rate),
               last_used_at = agg.last_used
          FROM agg
         WHERE p.id = agg.keep_id
        """
    )
    op.execute(
        f"""
        DELETE FROM {_TABLE}
         WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY tenant_id, goal_pattern
                    ORDER BY last_used_at DESC NULLS LAST, id
                ) AS rn
                  FROM {_TABLE}
            ) ranked
             WHERE rn > 1
         )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_procedural_memories_tenant_pattern "
        f"ON {_TABLE} (tenant_id, goal_pattern)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_procedural_memories_pattern_trgm "
        f"ON {_TABLE} USING gin (goal_pattern gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_procedural_memories_pattern_trgm")
    op.execute("DROP INDEX IF EXISTS uq_procedural_memories_tenant_pattern")
