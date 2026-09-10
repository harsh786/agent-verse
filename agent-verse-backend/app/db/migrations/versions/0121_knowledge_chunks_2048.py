"""knowledge_chunks_2048 — dimension table for 2048-d embedders (NVIDIA nemotron).

NVIDIA's ``nvidia/nemotron-3-embed-1b`` returns 2048-dimensional vectors, a
dimension the knowledge store now advertises (SUPPORTED_EMBEDDING_DIMENSIONS)
but for which no chunk table existed. Clone the current shape of an existing
dimension table so every column and index later migrations added is carried
over, then set the embedding column to VECTOR(2048).

Vector ANN index note: pgvector's HNSW/IVFFlat cap out at 2000 dimensions, so —
exactly as the existing 3072-d table does — this table gets no ``hnsw`` vector
index and no binary-quantize index; cosine search falls back to an exact scan.
All non-vector indexes (tenant, trigram, FTS, freshness, hierarchy, …) are
copied by ``INCLUDING ALL``.

Revision ID: 0121
Revises: 0120
"""

from __future__ import annotations

from alembic import op

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None

_DIM = 2048
_TABLE = f"knowledge_chunks_{_DIM}"
_TEMPLATE = "knowledge_chunks_1024"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE idx RECORD;
        BEGIN
            IF to_regclass('public.{_TABLE}') IS NOT NULL THEN
                RETURN;  -- already created by a prior run
            END IF;
            IF to_regclass('public.{_TEMPLATE}') IS NULL THEN
                RETURN;  -- template absent on a fresh DB — nothing to clone
            END IF;

            -- Clone columns, defaults, constraints and every index from the live
            -- template so we inherit all later-added columns without re-listing.
            EXECUTE 'CREATE TABLE public.{_TABLE} (LIKE public.{_TEMPLATE} INCLUDING ALL)';

            -- Drop the copied vector ANN indexes BEFORE retargeting the column:
            -- 2048 > pgvector's 2000-dim hnsw/ivfflat limit, so an hnsw or
            -- binary-quantize index on a vector(2048) column cannot be built.
            -- Cosine search falls back to exact scan (as the 3072-d table does).
            FOR idx IN
                SELECT indexname, indexdef FROM pg_indexes
                WHERE tablename = '{_TABLE}'
                  AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%'
                       OR indexdef ILIKE '%binary_quantize%')
            LOOP
                EXECUTE 'DROP INDEX IF EXISTS public.' || quote_ident(idx.indexname);
            END LOOP;

            -- Now retarget the embedding column to this table's dimension.
            EXECUTE 'ALTER TABLE public.{_TABLE} ALTER COLUMN embedding TYPE vector({_DIM})';
        END
        $$;
        """
    )

    # Row-level tenant isolation, mirroring the other dimension tables.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.{_TABLE}') IS NULL THEN
                RETURN;
            END IF;
            EXECUTE 'ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY';
            EXECUTE 'ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY';
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = '{_TABLE}' AND policyname = '{_TABLE}_isolation'
            ) THEN
                EXECUTE 'CREATE POLICY {_TABLE}_isolation ON public.{_TABLE} '
                        'USING (tenant_id::text = current_setting(''app.tenant_id'', TRUE))';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS public.{_TABLE} CASCADE")
