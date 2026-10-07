"""An on-prem embedding model configured ONLY through the Model Registry is the
one KB ingestion (worker path) and query embedding use. Real Postgres + Redis.

The operator registers ``Qwen/Qwen3-Embedding-0.6B`` with its own ``base_url``
and credential (``POST /models/configured``), runs "Test connection" and saves
the embedding preference order, all through the Model Registry API, persisted
in the shared Redis registry store. The Celery worker's ingestion builder
(``_build_worker_ingestion``) then must embed documents on that endpoint: a real
OpenAI-compatible ``/v1/embeddings`` server started in this test on 127.0.0.1
(a private address, allowed by ``ALLOW_PRIVATE_NETWORK_ACCESS``). The chunks
land in ``knowledge_chunks_1024`` carrying exactly the vectors that server
produced, and the collection records the model.

A model whose real width does not fit the index is refused on first use: the
document fails with the dimension error and nothing is written.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integration/test_registry_embedder_ingestion_pg.py -m integration
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.rag.models import KnowledgeCollection
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.rag._pg import admin_exec, seed_tenant

pytestmark = pytest.mark.integration

_ISOLATE_PROVIDER_ENV = True

_QWEN = "Qwen/Qwen3-Embedding-0.6B"
_NARROW = "acme/embed-narrow"  # not in the catalog; really 768-d
_WIDTHS = {_QWEN: 1024, _NARROW: 768}
_SECRET = "it-embed-key"
_ADMIN = {"X-API-Key": "ak_registry_admin"}
_ADMIN_CTX = TenantContext(
    tenant_id="t-operator", plan=PlanTier.ENTERPRISE, api_key_id="k", roles=("admin",)
)
_REGISTRY_KEYS = (
    "model_registry:configured",
    "model_registry:configured:version",
    "model_registry:preferences",
    "model_registry:probed_dimensions",
)
_BODY = (
    "Bramblewood Freight ships refrigerated containers from Kochi to Rotterdam every "
    "Tuesday. Claims for spoiled cargo must be filed within fourteen days of arrival "
    "with the signed bill of lading and the reefer temperature log attached."
)


def vector_for(text: str, dim: int) -> list[float]:
    """The deterministic embedding the test server returns for *text*."""
    out: list[float] = []
    seed = text.encode()
    counter = 0
    while len(out) < dim:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        out.extend(v / 65535.0 for v in struct.unpack(">16H", block))
        counter += 1
    return out[:dim]


class _EmbeddingsHandler(BaseHTTPRequestHandler):
    """A minimal OpenAI-compatible server: GET /v1/models, POST /v1/embeddings."""

    server: _EmbeddingsServer

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": m} for m in _WIDTHS]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/embeddings":
            self._send(404, {"error": "not found"})
            return
        if self.headers.get("Authorization") != f"Bearer {_SECRET}":
            self._send(401, {"error": "bad key"})
            return
        model = str(body.get("model") or "")
        if model not in _WIDTHS:
            self._send(404, {"error": f"model {model} not served"})
            return
        texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
        self.server.calls.append({"model": model, "input": list(texts)})
        self._send(
            200,
            {
                "object": "list",
                "model": model,
                "data": [
                    {"object": "embedding", "index": i, "embedding": vector_for(t, _WIDTHS[model])}
                    for i, t in enumerate(texts)
                ],
                "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
            },
        )

    def log_message(self, *_args: Any) -> None:  # keep the test output clean
        return


class _EmbeddingsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _EmbeddingsHandler)
        self.calls: list[dict[str, Any]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


@pytest.fixture
def embed_server() -> Iterator[_EmbeddingsServer]:
    server = _EmbeddingsServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def registry(test_backends: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The shared registry store on the Redis testcontainer, empty, as in a worker."""
    import redis

    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel
    from app.ai_router.registry import model_registry
    from app.ai_router.registry_store import ModelRegistryStore
    from app.core.config import get_settings

    _pg, redis_url = test_backends
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    get_settings.cache_clear()
    client = redis.from_url(redis_url, decode_responses=True)
    client.delete(*_REGISTRY_KEYS)
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(client))
    # Seed from the shared store on first selection, like a fresh process.
    monkeypatch.setattr(sel, "_lazy_seeded", False)
    monkeypatch.setattr(sel, "_seeded_version", None)
    monkeypatch.setattr(sel, "_last_version_check", 0.0)
    try:
        yield rs._store
    finally:
        client.delete(*_REGISTRY_KEYS)
        client.close()
        model_registry.clear_configured()
        model_registry.set_preferences({})
        get_settings.cache_clear()


def _api() -> TestClient:
    from app.api.model_registry import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _ADMIN_CTX if key == _ADMIN["X-API-Key"] else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    return TestClient(app)


def _configure_through_the_registry(api: TestClient, model_id: str, base_url: str) -> dict:
    body = {
        "provider": "onprem",
        "model_id": model_id,
        "display_name": model_id,
        "capabilities": ["embedding"],
        "base_url": base_url,
        "api_key": _SECRET,
    }
    saved = api.post("/models/configured", headers=_ADMIN, json=body)
    assert saved.status_code == 200, saved.text
    probe_body = {k: v for k, v in body.items() if k != "api_key"}  # the saved key is used
    probe = api.post("/models/configured/test-endpoint", headers=_ADMIN, json=probe_body)
    assert probe.status_code == 200, probe.text
    order = api.put(
        "/models/preferences/embedding", headers=_ADMIN, json={"order": [f"onprem/{model_id}"]}
    )
    assert order.status_code == 200, order.text
    return dict(probe.json())


async def _ingest(pg_url: str, pipeline: Any, tenant: str) -> tuple[Any, str]:
    from app.db.session import get_session_factory
    from app.rag.store import KnowledgeStore

    await seed_tenant(pg_url, tenant)
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k")
    collection_id = await KnowledgeStore(get_session_factory()).create_collection_async(
        KnowledgeCollection(name="freight-policies"), tenant_ctx=ctx
    )
    config = SourceConfig(
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant,
        name="policies",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id=collection_id,
        min_quality_score=0.0,
    )
    raw = RawDocument(
        doc_id=f"doc-{uuid.uuid4().hex[:8]}",
        source_id=config.source_id,
        tenant_id=tenant,
        content=_BODY.encode(),
        content_type="text/plain",
        title="Claims policy",
    )
    return await pipeline.ingest(raw, config), collection_id


async def _dispose_engines() -> None:
    import app.db.session as db_session

    for name in ("_engine", "_system_engine"):
        engine = getattr(db_session, name, None)
        if engine is not None:
            await engine.dispose()


async def test_worker_ingestion_and_query_embed_with_the_registry_configured_model(
    test_backends: tuple[str, str], registry: Any, embed_server: _EmbeddingsServer
) -> None:
    from app.ingestion.scheduler import _build_worker_ingestion
    from app.providers.embedder_factory import resolve_embedder

    pg_url, _redis = test_backends
    probe = _configure_through_the_registry(_api(), _QWEN, embed_server.base_url)
    assert probe["ok"] is True and probe["probe"] == "embedding", probe
    assert probe["dimensions"] == 1024 and probe["dimension_mismatch"] is False
    # The credential is in Redis only as vault ciphertext.
    saved = registry.get("onprem", _QWEN)
    assert saved["api_key_encrypted"] and _SECRET not in json.dumps(registry.list())
    assert saved["dimensions"] == 1024
    embed_server.calls.clear()

    tenant = f"t-regemb-{uuid.uuid4().hex[:8]}"
    try:
        _tracker, pipeline, _sources = _build_worker_ingestion()
        result, collection_id = await _ingest(pg_url, pipeline, tenant)
        assert result.status == "indexed", (result.status, result.error)
        assert result.chunks_created >= 1

        # The query side (API / retrieval) resolves the SAME model on the same server.
        query = resolve_embedder()
        assert (query.source, query.model, query.dimension) == ("registry", _QWEN, 1024)
        qvec = await query.embedder.embed_batch(["how long do I have to file a claim?"])
    finally:
        await _dispose_engines()

    # Documents were embedded on the registry endpoint, as that model.
    doc_calls = [c for c in embed_server.calls if any("Bramblewood" in t for t in c["input"])]
    assert doc_calls and {c["model"] for c in embed_server.calls} == {_QWEN}
    assert qvec[0] == vector_for("how long do I have to file a claim?", 1024)

    rows = await admin_exec(
        pg_url,
        "SELECT content, embedding::text, metadata->>'embedding_model' "
        "FROM knowledge_chunks_1024 WHERE tenant_id = :t AND collection_id = :c "
        "ORDER BY chunk_index",
        {"t": tenant, "c": collection_id},
    )
    assert rows, "no chunk was written to knowledge_chunks_1024"
    for content, embedding_text, model in rows:
        stored = [float(x) for x in embedding_text.strip("[]").split(",")]
        expected = vector_for(content, 1024)
        assert len(stored) == 1024 and model == _QWEN
        assert max(abs(a - b) for a, b in zip(stored, expected, strict=True)) < 1e-5
    (collection,) = await admin_exec(
        pg_url,
        "SELECT embedder, embedding_dim FROM knowledge_collections WHERE id = :c",
        {"c": collection_id},
    )
    assert tuple(collection) == (_QWEN, 1024)


async def test_a_registry_model_whose_width_does_not_fit_the_index_is_refused(
    test_backends: tuple[str, str], registry: Any, embed_server: _EmbeddingsServer
) -> None:
    """Unknown width, measured on first use: 768-d against a 1024-d index fails the
    document with the dimension error; nothing reaches any chunk table."""
    from app.ingestion.scheduler import _build_worker_ingestion

    pg_url, _redis = test_backends
    api = _api()
    # Saved and preferred WITHOUT a probe, so the width is unknown until first use.
    saved = api.post("/models/configured", headers=_ADMIN, json={
        "provider": "onprem", "model_id": _NARROW, "capabilities": ["embedding"],
        "base_url": embed_server.base_url, "api_key": _SECRET})
    assert saved.status_code == 200, saved.text
    assert api.put("/models/preferences/embedding", headers=_ADMIN,
                   json={"order": [f"onprem/{_NARROW}"]}).status_code == 200

    tenant = f"t-regnarrow-{uuid.uuid4().hex[:8]}"
    try:
        _tracker, pipeline, _sources = _build_worker_ingestion()
        result, collection_id = await _ingest(pg_url, pipeline, tenant)
    finally:
        await _dispose_engines()

    assert result.status == "failed", result.status
    assert "768-d vectors but the vector index is 1024-d" in str(result.error)
    assert embed_server.calls and {c["model"] for c in embed_server.calls} == {_NARROW}
    for dim in (768, 1024):
        (count,) = await admin_exec(
            pg_url,
            f"SELECT count(*) FROM knowledge_chunks_{dim} WHERE collection_id = :c",
            {"c": collection_id},
        )
        assert count[0] == 0
    # The mismatched width was not recorded as the model's width.
    assert "dimensions" not in registry.get("onprem", _NARROW)
