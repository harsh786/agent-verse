"""Binary-quantized Hamming HNSW indexes on knowledge_chunks_<dim>.

Adds a pgvector *binary* index for cheap first-stage retrieval: an HNSW index on
``binary_quantize(embedding)::bit(<dim>)`` with ``bit_hamming_ops``. Binary codes
are 32x smaller than float32 and Hamming distance is fast, so this powers a
coarse shortlist that a full-precision cosine pass then reranks.

Safe + additive:
  * Requires pgvector >= 0.7 (``binary_quantize`` / ``bit_hamming_ops``). The
    version is checked first; on an older extension the migration is a no-op
    (logged), so it never fails a deploy on an old image.
  * Expression index — no new column, no data rewrite. Existing queries are
    unaffected until a caller opts into the binary first-stage.
  * Built CONCURRENTLY (like the existing halfvec index) to avoid write locks.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None

_DIMENSIONS = (768, 1024, 1536, 3072)


def _pgvector_supports_binary() -> bool:
    """True when the installed pgvector is >= 0.7.0 (has binary_quantize)."""
    version = (
        op.get_bind()
        .execute(sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        .scalar_one_or_none()
    )
    if not version:
        return False
    try:
        parts = tuple(int(p) for p in str(version).split(".")[:3])
    except ValueError:
        return False
    return parts >= (0, 7, 0)


def _index_name(dimension: int) -> str:
    return f"idx_knowledge_chunks_{dimension}_binary_hamming"


def _create_index_concurrently(index_name: str, statement: str) -> None:
    """Retry an interrupted concurrent build without replacing a valid index."""
    is_valid = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT index.indisvalid FROM pg_index AS index "
                "JOIN pg_class AS relation ON relation.oid = index.indexrelid "
                "WHERE relation.relname = :index_name"
            ),
            {"index_name": index_name},
        )
        .scalar_one_or_none()
    )
    if is_valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
    op.execute(statement)


def upgrade() -> None:
    if not _pgvector_supports_binary():
        # Old pgvector — skip; a later upgrade to >= 0.7 can re-run this build.
        op.execute(
            "DO $$ BEGIN RAISE NOTICE "
            "'0120: pgvector < 0.7 — skipping binary_quantize indexes'; END $$"
        )
        return
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            table = f"knowledge_chunks_{dimension}"
            _create_index_concurrently(
                _index_name(dimension),
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_index_name(dimension)} "
                f"ON {table} USING hnsw "
                f"((binary_quantize(embedding)::bit({dimension})) bit_hamming_ops) "
                f"WITH (m = 16, ef_construction = 64)",
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for dimension in _DIMENSIONS:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_index_name(dimension)}")
