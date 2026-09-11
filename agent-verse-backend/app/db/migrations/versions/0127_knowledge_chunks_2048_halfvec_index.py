"""knowledge_chunks_2048 — bring it to parity with the other chunk tables.

``knowledge_chunks_2048`` (migration 0121, for the 2048-d NVIDIA nemotron
embedder) was created with two gaps versus every other ``knowledge_chunks_<dim>``
table:

1. **No vector ANN index.** The 0121 comment justified this with "exactly as the
   existing 3072-d table does" — but that premise is wrong: the 3072-d table DOES
   have an HNSW cosine index, built over ``embedding::halfvec(3072)`` (pgvector's
   ``halfvec`` supports HNSW up to 4000 dims, past the 2000-dim cap on plain
   ``vector``). So 2048 was the lone dimension doing an exact sequential scan for
   every cosine search — a real retrieval-latency gap for nemotron.

2. **A weaker RLS write policy.** The 768/1024/1536/3072 policies carry a
   ``WITH CHECK`` that verifies, on INSERT/UPDATE, that the row's collection is an
   active collection owned by the same tenant. The 2048 policy shipped with no
   explicit ``WITH CHECK`` (so Postgres falls back to the ``USING`` clause, which
   only checks ``tenant_id``) — meaning a caller could write chunks into the 2048
   table referencing a collection that is not theirs / not active, a hole the
   other tables close.

Both are tenant/parity correctness issues, and the RAG persistence readiness
probe (``app/rag/gateway.py``) asserts the uniform "every chunk table is HNSW-
cosine indexed" invariant — without the index it reported
``persistence_unavailable`` for every tenant once the 2048 table landed. This
migration closes both gaps.

Revision ID: 0127
Revises: 0126
"""

from __future__ import annotations

from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None

_TABLE = "knowledge_chunks_2048"
_INDEX = "idx_knowledge_chunks_2048_vector_halfvec"


def upgrade() -> None:
    # 1. RLS write policy: match the canonical per-chunk-table policy (0091) so
    #    writes are validated against active, tenant-owned collections. Plain DDL,
    #    runs inside the migration transaction.
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK ("
        "tenant_id = current_setting('app.tenant_id', TRUE) AND "
        "EXISTS (SELECT 1 FROM knowledge_collections AS collection "
        f"WHERE collection.id = {_TABLE}.collection_id "
        f"AND collection.tenant_id = {_TABLE}.tenant_id "
        "AND collection.is_active IS TRUE)"
        ")"
    )

    # 2. Vector ANN index: CREATE INDEX CONCURRENTLY cannot run inside a
    #    transaction — mirror the 3072 table's halfvec index build (migration 0091).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            f"{_INDEX} ON {_TABLE} "
            "USING hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
    # Restore the weaker 0121-era policy (tenant_id-only, no collection WITH CHECK).
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_isolation ON {_TABLE} "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )
