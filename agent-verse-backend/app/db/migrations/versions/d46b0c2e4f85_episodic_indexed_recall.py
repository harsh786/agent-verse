"""episodic_memories: indexed recall — vector column + trigram + quality window (MEM-40)

Episodic recall cast every episode's JSONB embedding to a vector and computed
``word_similarity`` over every row of the tenant, with no index, on each
planner call.

* ``embedding_vec vector(2048)`` (+ ``embedding_dim`` / ``embedding_model``):
  narrower embeddings are zero-padded (cosine unchanged), exactly as
  ``long_term_memory`` does; an HNSW index over ``embedding_vec::halfvec(2048)``
  (plain vector HNSW caps at 2000 dims) serves the similarity query.
  Backfilled from the JSONB column in batches.
* GIN ``gin_trgm_ops`` on ``goal_text`` for the ``%`` / ``<%`` operators.
* ``(tenant_id, quality_score DESC, created_at DESC)`` for the quality window.

Indexes are built CONCURRENTLY (no write lock on a large table).

Revision ID: d46b0c2e4f85
Revises: a4d51be05bf1
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d46b0c2e4f85"
down_revision: str | None = "a4d51be05bf1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIM = 2048
_BATCH = 5_000


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "ALTER TABLE episodic_memories "
        f"ADD COLUMN IF NOT EXISTS embedding_vec vector({_DIM}), "
        "ADD COLUMN IF NOT EXISTS embedding_dim SMALLINT, "
        "ADD COLUMN IF NOT EXISTS embedding_model TEXT"
    )
    with op.get_context().autocommit_block():
        # FORCE RLS hides rows from the owner; lift it for the backfill only
        # (the app role stays bound by the policy: ENABLE RLS is unchanged).
        op.execute("ALTER TABLE episodic_memories NO FORCE ROW LEVEL SECURITY")
        backfill = (
            "UPDATE episodic_memories AS e SET "
            f"embedding_vec = CAST(array_cat(src.v, array_fill(0::float4, "
            f"ARRAY[{_DIM} - src.n])) AS vector({_DIM})), embedding_dim = src.n "
            "FROM (SELECT id, jsonb_array_length(embedding) AS n, "
            "  ARRAY(SELECT x::float4 FROM jsonb_array_elements_text(embedding) AS x) AS v "
            "  FROM episodic_memories WHERE embedding_vec IS NULL "
            "  AND jsonb_typeof(embedding) = 'array' "
            f"  AND jsonb_array_length(embedding) BETWEEN 1 AND {_DIM} "
            f"  LIMIT {_BATCH}) AS src WHERE e.id = src.id"
        )
        conn = op.get_bind()
        while True:
            result = conn.exec_driver_sql(backfill)
            if (result.rowcount or 0) < _BATCH:
                break
        op.execute("ALTER TABLE episodic_memories FORCE ROW LEVEL SECURITY")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_episodic_embedding_halfvec "
            f"ON episodic_memories USING hnsw ((embedding_vec::halfvec({_DIM})) "
            "halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_episodic_goal_text_trgm "
            "ON episodic_memories USING gin (goal_text gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_episodic_tenant_quality "
            "ON episodic_memories (tenant_id, quality_score DESC, created_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_episodic_tenant_quality")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_episodic_goal_text_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_episodic_embedding_halfvec")
    op.execute(
        "ALTER TABLE episodic_memories DROP COLUMN IF EXISTS embedding_model, "
        "DROP COLUMN IF EXISTS embedding_dim, DROP COLUMN IF EXISTS embedding_vec"
    )
