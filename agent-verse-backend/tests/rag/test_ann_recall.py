"""Million-doc RAG T2 — ANN recall harness.

Proves the HNSW vector index returns nearly the same neighbours as an exact
brute-force scan (recall@k), so retrieval quality does not silently degrade as a
collection grows. Uses a self-contained pgvector container (no app wiring).
Marked integration + slow.
"""
from __future__ import annotations

import math
import random

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_DIM = 768
_N = 2000
_K = 10
_QUERIES = 20
_CLUSTERS = 40


def _seeded_vectors() -> list[list[float]]:
    rng = random.Random(1234)
    centers = [[rng.gauss(0, 1) for _ in range(_DIM)] for _ in range(_CLUSTERS)]
    vecs: list[list[float]] = []
    for i in range(_N):
        c = centers[i % _CLUSTERS]
        vecs.append([c[d] + rng.gauss(0, 0.15) for d in range(_DIM)])
    return vecs


def _lit(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def test_hnsw_recall_matches_bruteforce() -> None:
    import asyncpg
    from testcontainers.postgres import PostgresContainer

    vecs = _seeded_vectors()
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        raw = pg.get_connection_url().replace("postgresql+asyncpg://", "")
        conn = await asyncpg.connect(f"postgresql://{raw}")
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(f"CREATE TABLE probe (id int primary key, embedding vector({_DIM}))")
            await conn.executemany(
                "INSERT INTO probe(id, embedding) VALUES($1, $2::vector)",
                [(i, _lit(v)) for i, v in enumerate(vecs)],
            )
            await conn.execute(
                "CREATE INDEX ON probe USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )

            total_recall = 0.0
            rng = random.Random(99)
            for _ in range(_QUERIES):
                q = _lit(vecs[rng.randrange(_N)])
                # Exact: force a sequential scan (no index) → true nearest neighbours.
                async with conn.transaction():
                    await conn.execute("SET LOCAL enable_indexscan = off")
                    await conn.execute("SET LOCAL enable_bitmapscan = off")
                    exact = {
                        r["id"]
                        for r in await conn.fetch(
                            f"SELECT id FROM probe ORDER BY embedding <=> $1::vector LIMIT {_K}", q
                        )
                    }
                # ANN: HNSW index with a healthy ef_search.
                async with conn.transaction():
                    await conn.execute("SET LOCAL hnsw.ef_search = 128")
                    ann = {
                        r["id"]
                        for r in await conn.fetch(
                            f"SELECT id FROM probe ORDER BY embedding <=> $1::vector LIMIT {_K}", q
                        )
                    }
                total_recall += len(exact & ann) / _K

            mean_recall = total_recall / _QUERIES
            assert mean_recall >= 0.9, f"HNSW recall@{_K} too low: {mean_recall:.3f}"
            assert not math.isnan(mean_recall)
        finally:
            await conn.close()
