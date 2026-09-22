"""pgvector-extension-unavailable fallback — real DB, no app wiring.

Complements ``test_ann_recall.py`` (HNSW recall) and the dimension-mismatch
coverage in ``test_persisted_rag_store.py``: this covers the third genuinely
untested pgvector-specific fault, the vector leg running against a database
that never had the ``vector`` extension installed (a plain ``postgres:16``
image, not ``pgvector/pgvector``). ``CAST(:emb AS vector)`` then fails with a
real "type \"vector\" does not exist" error from Postgres itself — not a
mocked exception — exercising the exact except-block in
``app.rag.engine.hybrid_search`` that production traffic would hit if a
collection's vector index/extension were ever missing.

Marked integration + slow: spins up its own throwaway container.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]


async def test_vector_leg_falls_back_when_pgvector_extension_missing() -> None:
    import asyncpg
    from sqlalchemy.ext.asyncio import create_async_engine
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    from app.rag.engine import hybrid_search

    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        raw = pg.get_connection_url().replace("postgresql+asyncpg://", "")
        conn = await asyncpg.connect(f"postgresql://{raw}")
        try:
            # No `CREATE EXTENSION vector` — this database genuinely has no
            # pgvector support, unlike the pgvector/pgvector image every other
            # RAG integration test uses. `embedding` is a plain text column so
            # table creation itself succeeds; only the `vector` cast fails.
            await conn.execute(
                """
                CREATE TABLE knowledge_chunks_768 (
                    id uuid primary key default gen_random_uuid(),
                    collection_id text not null,
                    content text not null,
                    metadata jsonb,
                    embedding text,
                    expires_at timestamptz
                )
                """
            )
            await conn.execute(
                "INSERT INTO knowledge_chunks_768 (collection_id, content) "
                "VALUES ('col-1', 'unreachable without a vector cast')"
            )
        finally:
            await conn.close()

        engine = create_async_engine(f"postgresql+asyncpg://{raw}")
        try:
            async with engine.connect() as connection:
                from sqlalchemy.ext.asyncio import AsyncSession

                async with AsyncSession(bind=connection) as session, session.begin():
                    results = await hybrid_search(
                        session,
                        query="unreachable",
                        query_embedding=[0.1] * 768,
                        collection_id="col-1",
                        embedding_dim=768,
                        retrieval_mode="vector",
                        strict=False,
                    )
            assert results == []
        finally:
            await engine.dispose()


async def test_vector_leg_raises_strict_when_pgvector_extension_missing() -> None:
    import asyncpg
    from sqlalchemy.ext.asyncio import create_async_engine
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    from app.rag.engine import RetrievalLegExecutionError, hybrid_search

    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        raw = pg.get_connection_url().replace("postgresql+asyncpg://", "")
        conn = await asyncpg.connect(f"postgresql://{raw}")
        try:
            await conn.execute(
                """
                CREATE TABLE knowledge_chunks_768 (
                    id uuid primary key default gen_random_uuid(),
                    collection_id text not null,
                    content text not null,
                    metadata jsonb,
                    embedding text,
                    expires_at timestamptz
                )
                """
            )
            await conn.execute(
                "INSERT INTO knowledge_chunks_768 (collection_id, content) "
                "VALUES ('col-1', 'unreachable without a vector cast')"
            )
        finally:
            await conn.close()

        engine = create_async_engine(f"postgresql+asyncpg://{raw}")
        try:
            from sqlalchemy.ext.asyncio import AsyncSession

            with pytest.raises(RetrievalLegExecutionError, match="vector") as exc_info:
                async with engine.connect() as connection:
                    async with AsyncSession(bind=connection) as session, session.begin():
                        await hybrid_search(
                            session,
                            query="unreachable",
                            query_embedding=[0.1] * 768,
                            collection_id="col-1",
                            embedding_dim=768,
                            retrieval_mode="vector",
                            strict=True,
                        )
            assert exc_info.value.leg == "vector"
        finally:
            await engine.dispose()
