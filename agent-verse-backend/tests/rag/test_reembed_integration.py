"""KB-25 on real PostgreSQL + pgvector: re-embedding rewrites a collection's
chunks with the new embedder — including onto a new vector dimension.

The worker task used a global embedding router whose provider is never set in
the worker and refused any dimension change, so a deployment that switched
embedders (almost always a width change) could never re-embed.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.rag import reembed
from app.rag.reembed import ReembedError, ReembedProgress
from tests.rag.reembed_fakes import FakeRedis

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield url


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def db(postgres_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(postgres_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _collection(db: Any, *, dim: int, n_chunks: int) -> tuple[str, str]:
    tenant_id = f"t-{uuid.uuid4().hex[:10]}"
    collection_id = uuid.uuid4().hex
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'enterprise', true)"
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
        await session.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, name, embedding_dim) "
                "VALUES (:id, :tid, :id, :dim)"
            ),
            {"id": collection_id, "tid": tenant_id, "dim": dim},
        )
        for index in range(n_chunks):
            await session.execute(
                text(
                    f"INSERT INTO knowledge_chunks_{dim} (id, tenant_id, collection_id, "
                    "document_id, chunk_index, content, content_hash, metadata, embedding) "
                    "VALUES (:id, :tid, :cid, 'doc-1', :idx, :content, :hash, "
                    "CAST(:meta AS jsonb), CAST(:vec AS vector))"
                ),
                {
                    "id": f"chunk-{index:03d}-{collection_id[:6]}",
                    "tid": tenant_id,
                    "cid": collection_id,
                    "idx": index,
                    "content": f"paragraph {index}",
                    "hash": uuid.uuid4().hex,
                    "meta": f'{{"page": {index}}}',
                    "vec": "[" + ",".join(["0.01"] * dim) + "]",
                },
            )
    return tenant_id, collection_id


def _embedder(dim: int) -> Any:
    calls: list[list[str]] = []

    async def embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.5 + 0.001 * i] * dim for i, _ in enumerate(texts)]

    embed.calls = calls  # type: ignore[attr-defined]
    return embed


async def _rows(db: Any, dim: int, tenant_id: str, collection_id: str) -> list[tuple[Any, ...]]:
    async with db() as session:
        result = await session.execute(
            text(
                f"SELECT id, content, metadata, vector_dims(embedding), "
                f"(embedding::real[])[1] FROM knowledge_chunks_{dim} "
                "WHERE collection_id = :cid AND tenant_id = :tid ORDER BY id"
            ),
            {"cid": collection_id, "tid": tenant_id},
        )
        return [tuple(r) for r in result.fetchall()]


async def _collection_dim(db: Any, collection_id: str) -> tuple[int, str | None]:
    async with db() as session:
        row = (
            await session.execute(
                text("SELECT embedding_dim, embedder FROM knowledge_collections WHERE id = :cid"),
                {"cid": collection_id},
            )
        ).one()
        return int(row[0]), row[1]


async def test_same_dimension_rewrites_vectors_in_place(db: Any) -> None:
    tenant_id, collection_id = await _collection(db, dim=768, n_chunks=5)
    result = await reembed.re_embed_collection(
        db=db,
        embed=_embedder(768),
        model_key="dedicated/new-768",
        tenant_id=tenant_id,
        collection_id=collection_id,
        batch_size=2,
    )
    assert result["re_embedded"] == 5
    assert result["dimension"] == result["previous_dimension"] == 768
    rows = await _rows(db, 768, tenant_id, collection_id)
    assert len(rows) == 5
    assert all(abs(float(r[4]) - 0.5) < 0.01 for r in rows)
    assert await _collection_dim(db, collection_id) == (768, "dedicated/new-768")


async def test_new_dimension_moves_every_chunk_to_the_new_table(db: Any) -> None:
    tenant_id, collection_id = await _collection(db, dim=768, n_chunks=7)
    before = await _rows(db, 768, tenant_id, collection_id)
    redis = FakeRedis()
    progress = ReembedProgress(redis, tenant_id, collection_id, "job-1")
    result = await reembed.re_embed_collection(
        db=db,
        embed=_embedder(1024),
        model_key="voyage/voyage-3",
        tenant_id=tenant_id,
        collection_id=collection_id,
        progress=progress,
        batch_size=3,
    )
    assert result["re_embedded"] == 7
    assert (result["previous_dimension"], result["dimension"]) == (768, 1024)
    after = await _rows(db, 1024, tenant_id, collection_id)
    assert [r[0] for r in after] == [r[0] for r in before]  # same ids
    assert [(r[1], r[2]) for r in after] == [(r[1], r[2]) for r in before]  # content kept
    assert all(r[3] == 1024 for r in after)
    assert await _rows(db, 768, tenant_id, collection_id) == []  # old rows gone
    assert await _collection_dim(db, collection_id) == (1024, "voyage/voyage-3")
    assert progress.state["status"] == "running"  # the task marks completion
    assert progress.state["processed"] == 7 and progress.state["total"] == 7


async def test_unsupported_dimension_is_refused_and_leaves_the_collection_intact(
    db: Any,
) -> None:
    tenant_id, collection_id = await _collection(db, dim=768, n_chunks=3)
    before = await _rows(db, 768, tenant_id, collection_id)
    with pytest.raises(ReembedError, match="384"):
        await reembed.re_embed_collection(
            db=db,
            embed=_embedder(384),
            model_key="local/mini",
            tenant_id=tenant_id,
            collection_id=collection_id,
        )
    assert await _rows(db, 768, tenant_id, collection_id) == before
    assert (await _collection_dim(db, collection_id))[0] == 768


async def test_another_tenant_cannot_re_embed_the_collection(db: Any) -> None:
    tenant_id, collection_id = await _collection(db, dim=768, n_chunks=2)
    with pytest.raises(ReembedError, match="not found"):
        await reembed.re_embed_collection(
            db=db,
            embed=_embedder(1024),
            model_key="m",
            tenant_id="t-intruder",
            collection_id=collection_id,
        )
    assert len(await _rows(db, 768, tenant_id, collection_id)) == 2


async def test_interrupted_move_is_resumable_and_reconciles_changes(db: Any) -> None:
    tenant_id, collection_id = await _collection(db, dim=768, n_chunks=4)
    calls = 0

    async def flaky(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("provider timeout")
        return [[0.25] * 1024 for _ in texts]

    with pytest.raises(RuntimeError, match="provider timeout"):
        await reembed.re_embed_collection(
            db=db,
            embed=flaky,
            model_key="m",
            tenant_id=tenant_id,
            collection_id=collection_id,
            batch_size=2,
        )
    # Still served from the old table on its old dimension.
    assert (await _collection_dim(db, collection_id))[0] == 768
    assert len(await _rows(db, 768, tenant_id, collection_id)) == 4
    # Meanwhile a chunk that was already copied is deleted from the live table.
    async with db() as session, session.begin():
        await session.execute(
            text("DELETE FROM knowledge_chunks_768 WHERE id = :id"),
            {"id": f"chunk-000-{collection_id[:6]}"},
        )
    result = await reembed.re_embed_collection(
        db=db,
        embed=_embedder(1024),
        model_key="m",
        tenant_id=tenant_id,
        collection_id=collection_id,
        batch_size=2,
    )
    assert result["dimension"] == 1024
    moved = await _rows(db, 1024, tenant_id, collection_id)
    assert [r[0] for r in moved] == [f"chunk-{i:03d}-{collection_id[:6]}" for i in (1, 2, 3)]
    assert await _rows(db, 768, tenant_id, collection_id) == []


async def test_empty_collection_moves_to_the_embedders_dimension(db: Any) -> None:
    tenant_id, collection_id = await _collection(db, dim=768, n_chunks=0)
    result = await reembed.re_embed_collection(
        db=db,
        embed=_embedder(1536),
        model_key="openai/text-embedding-3-small",
        tenant_id=tenant_id,
        collection_id=collection_id,
    )
    assert result["re_embedded"] == 0
    assert (await _collection_dim(db, collection_id))[0] == 1536
