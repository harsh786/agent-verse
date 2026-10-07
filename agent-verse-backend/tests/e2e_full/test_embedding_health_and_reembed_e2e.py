"""e2e_full: embedding health and re-embedding must read the tables that exist.

``knowledge_chunks`` has never existed in this schema. Migration 0062 creates
``knowledge_chunks_768 / _1024 / _1536 / _2048 / _3072`` — one table per
embedding dimension — and ``app.rag.store._chunk_table(dim)`` is how every
working path resolves the right one.

Two places queried the bare name anyway, and both swallowed the resulting
``UndefinedTableError``:

* ``GET /embeddings/health/{collection_id}`` — the ``except Exception: pass``
  around its stats query meant it reported *every* collection as
  ``total_chunks: 0, coverage_pct: 0.0, needs_reembed: true``, with the model
  left at a hardcoded default. It has never returned a real number.
  (``MAX(updated_at)`` was doubly wrong: the chunk tables have ``created_at``,
  not ``updated_at``.)

* ``re_embed_collection`` — the Celery task the re-embedding policy exists to
  trigger — returned ``{"error": ...}`` from its own broad except while Celery
  recorded the task as successful. It has never re-embedded a chunk. It also ran
  without RLS context, updated by bare ``id`` with no tenant scope, and loaded
  every chunk in the collection into memory with a single ``fetchall()``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _collection_with_chunks(app: Any, tenant_client: Any, n_chunks: int) -> tuple[str, str]:
    """Create a collection with ``n_chunks`` real chunks; return (tenant_id, id)."""
    from app.rag.models import Chunk
    from app.tenancy.context import PlanTier, TenantContext

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    resp = await tenant_client.post(
        "/knowledge/collections",
        json={"name": f"emb-{uuid.uuid4().hex[:8]}", "description": "embedding audit"},
    )
    assert resp.status_code in (200, 201), resp.text
    collection_id = str(resp.json()["collection_id"])

    ctx = TenantContext(tenant_id=tenant_id, api_key_id="emb", plan=PlanTier.FREE)
    store = app.state.knowledge_store
    dim = int(await store.get_collection_embedding_dim(collection_id, tenant_ctx=ctx) or 1536)

    doc_id = str(uuid.uuid4())
    await store.ingest_chunks_async(
        [
            Chunk(
                chunk_id=uuid.uuid4().hex,
                document_id=doc_id,
                content=f"runbook paragraph {i}: restart the ingest worker",
                embedding=[0.05 + 0.001 * i] * dim,
                metadata={"doc_content_hash": f"emb-{doc_id}"},
                chunk_index=i,
            )
            for i in range(n_chunks)
        ],
        collection_id=collection_id,
        tenant_ctx=ctx,
    )
    return tenant_id, collection_id


async def test_embedding_health_reports_real_numbers(app: Any, tenant_client: Any) -> None:
    _tenant_id, collection_id = await _collection_with_chunks(app, tenant_client, 4)

    resp = await tenant_client.get(f"/embeddings/health/{collection_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_chunks"] == 4, (
        f"health reported {body['total_chunks']} chunks for a collection with 4 — "
        "it is querying a table that does not exist and swallowing the error"
    )
    assert body["embedded_chunks"] == 4
    assert body["coverage_pct"] == pytest.approx(100.0)
    assert body["embedding_dim"] is not None
    assert body["last_embedded_at"] is not None
    # Every chunk shares a near-identical vector, so similarity to the centroid
    # is ~1.0 — i.e. an actual semantic signal, not the coverage fallback.
    assert body["avg_similarity"] is not None
    assert body["avg_similarity"] > 0.9
    assert body["needs_reembed"] is False


async def test_embedding_health_is_tenant_scoped(app: Any, client: Any) -> None:
    from httpx import ASGITransport, AsyncClient

    async def _tenant() -> Any:
        email = f"emb-{uuid.uuid4().hex[:12]}@example.com"
        r = await client.post("/tenants/signup", json={"name": "Emb", "email": email})
        assert r.status_code == 201, r.text
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://e2e-full",
            headers={"X-API-Key": r.json()["api_key"]},
        )

    async with await _tenant() as ca:
        _t, collection_id = await _collection_with_chunks(app, ca, 3)
        assert (await ca.get(f"/embeddings/health/{collection_id}")).json()["total_chunks"] == 3

    async with await _tenant() as cb:
        other = await cb.get(f"/embeddings/health/{collection_id}")
    assert other.status_code == 200
    assert other.json()["total_chunks"] == 0, (
        "another tenant read this collection's embedding stats"
    )


def _install_embedder(monkeypatch: pytest.MonkeyPatch, dim: int) -> None:
    """Make the worker's ``resolve_embedder()`` return a ``dim``-wide embedder.

    KB-25: the task builds the deployment embedder itself (it used the global
    embedding router, whose provider is never set in the worker). What these
    tests exercise is table resolution, tenant scoping, pagination and the write
    path — not a real provider.
    """
    from app.providers.base import EmbedResponse
    from app.providers.embedder_factory import EmbedderResolution

    class _Embedder:
        async def embed(self, request: Any) -> EmbedResponse:
            return EmbedResponse(
                embeddings=[[0.9 - 0.0001 * i] * dim for i, _ in enumerate(request.texts)]
            )

    monkeypatch.setattr(
        "app.providers.embedder_factory.resolve_embedder",
        # Same signature as resolve_embedder (a worker passes wire_registry_store=True).
        lambda settings=None, *, wire_registry_store=False: EmbedderResolution(
            embedder=_Embedder(), provider="e2e", model=f"fake-{dim}", dimension=dim
        ),
    )


@pytest.fixture
def _dimension_matched_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_embedder(monkeypatch, 1536)


async def test_re_embed_collection_actually_rewrites_vectors(
    app: Any, tenant_client: Any, _dimension_matched_embedder: Any
) -> None:
    """The backfill task must report a real count and change the stored vectors."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.scaling.tasks import re_embed_collection_async

    tenant_id, collection_id = await _collection_with_chunks(app, tenant_client, 5)

    async def _vectors() -> list[str]:
        async with (
            app.state.db_session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            dim = (
                await session.execute(
                    text(
                        "SELECT embedding_dim FROM knowledge_collections "
                        "WHERE id = :cid AND tenant_id = :tid"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).scalar_one()
            rows = (
                await session.execute(
                    text(
                        f"SELECT embedding::text FROM knowledge_chunks_{int(dim)} "
                        "WHERE collection_id = :cid AND tenant_id = :tid ORDER BY chunk_index"
                    ),
                    {"cid": collection_id, "tid": tenant_id},
                )
            ).fetchall()
            return [str(r[0]) for r in rows]

    before = await _vectors()
    assert len(before) == 5

    result = await re_embed_collection_async(tenant_id, collection_id)
    assert "error" not in result, f"re-embed failed: {result}"
    assert result["re_embedded"] == 5, (
        f"re-embed reported {result.get('re_embedded')} of 5 chunks — it is "
        "querying a table that does not exist and returning its own error dict "
        "while Celery records success"
    )

    after = await _vectors()
    assert len(after) == 5
    assert after != before, "re-embed reported success but no vector changed"


async def test_re_embed_refuses_an_unsupported_dimension(
    app: Any, tenant_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model whose width has no chunk table is refused, not half-applied.

    (A move to another SUPPORTED width is a real re-embed now — KB-25; it is
    covered on Postgres in tests/rag/test_reembed_integration.py.)
    """
    from app.scaling.tasks import re_embed_collection_async

    tenant_id, collection_id = await _collection_with_chunks(app, tenant_client, 2)

    # A provider producing 384-d vectors: no knowledge_chunks_384 table exists,
    # so the run is refused before anything is written.
    _install_embedder(monkeypatch, 384)
    result = await re_embed_collection_async(tenant_id, collection_id)
    assert result["re_embedded"] == 0
    assert "dimension" in result["error"] or "-dim" in result["error"]


async def test_re_embed_is_tenant_scoped_and_batched(
    app: Any, tenant_client: Any, _dimension_matched_embedder: Any
) -> None:
    """A re-embed must not touch another tenant, and must not slurp the table."""
    import inspect

    from app.scaling import tasks as _tasks

    tenant_id, collection_id = await _collection_with_chunks(app, tenant_client, 3)

    # Another tenant's id must find nothing rather than re-embedding these rows.
    foreign = await _tasks.re_embed_collection_async(
        f"tenant-{uuid.uuid4().hex[:12]}", collection_id
    )
    assert foreign.get("re_embedded") == 0, (
        f"re-embed under a foreign tenant touched {foreign.get('re_embedded')} chunks"
    )

    mine = await _tasks.re_embed_collection_async(tenant_id, collection_id)
    assert mine["re_embedded"] == 3

    from app.rag import reembed as _reembed

    source = inspect.getsource(_reembed)
    assert "fetchall()" not in source or "LIMIT" in source, (
        "re-embed loads the whole collection into memory; at millions of chunks "
        "that is an OOM, not a backfill"
    )
    assert "sqlalchemy_rls_context" in source, (
        "re-embed runs without RLS context — under the app's own least-privilege "
        "role it matches zero rows and reports success"
    )
