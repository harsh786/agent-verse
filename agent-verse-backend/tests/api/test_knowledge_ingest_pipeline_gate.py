"""Regression: direct /knowledge ingest routes bypassed the ingestion pipeline.

Despite the "LAW-01: single pipeline path" comment, ``/knowledge/ingest``,
``/ingest/file``, ``/ingest/url``, ``/ingest/openapi`` and the legacy
per-source ingestors (pdf/docx/github/confluence/jira/slack via
``_ingest_chunks_from_source``) parsed, chunked, embedded and persisted on their
own: no Stage 6 PII redaction, no Stage 6b RAG_INGEST guardrail, no Stage 3
dedup. The raw SSN below reached the embedder and the vector store verbatim, and
re-posting the same document duplicated every chunk.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.guardrails_v2.engine import guardrails_engine
from app.rag.models import KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_TENANT = "tid-ingest-gate"
_CTX = TenantContext(tenant_id=_TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="kid-gate")
_KEY = "av_test_ingest_gate"
_HDRS = {"X-API-Key": _KEY}
_SSN = "123-45-6789"
_BODY = (
    "Onboarding record for the quarterly review. The applicant social security "
    f"number is {_SSN}; every other field was verified by the operations team."
)


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.seen.extend(request.texts)
        return EmbedResponse(embeddings=[[0.5] * 768 for _ in request.texts], model="fake")


def _app() -> tuple[TestClient, KnowledgeStore, _Embedder, str]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(knowledge_router)
    store = KnowledgeStore()
    collection = KnowledgeCollection(name="gate")
    store.create_collection(collection, tenant_ctx=_CTX)
    embedder = _Embedder()
    app.state.knowledge_store = store
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = embedder
    return TestClient(app), store, embedder, collection.collection_id


def _stored_text(store: KnowledgeStore, collection_id: str) -> str:
    return "\n".join(c.content for c in store._data[(_TENANT, collection_id)].chunks)


def test_ingest_redacts_pii_before_embedding_and_storage() -> None:
    client, store, embedder, cid = _app()
    resp = client.post(
        "/knowledge/ingest", json={"collection_id": cid, "content": _BODY}, headers=_HDRS
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["chunks_created"] >= 1
    assert all(_SSN not in t for t in embedder.seen)
    stored = _stored_text(store, cid)
    assert _SSN not in stored and "[REDACTED:SSN]" in stored


def test_ingest_dedups_an_identical_document() -> None:
    client, store, _, cid = _app()
    first = client.post(
        "/knowledge/ingest", json={"collection_id": cid, "content": _BODY}, headers=_HDRS
    )
    second = client.post(
        "/knowledge/ingest", json={"collection_id": cid, "content": _BODY}, headers=_HDRS
    )
    assert first.status_code == second.status_code == 201
    assert second.json()["chunks_created"] == 0
    assert second.json()["deduplicated"] is True
    assert len(store._data[(_TENANT, cid)].chunks) == first.json()["chunks_created"]


def test_ingest_rag_ingest_guardrail_blocks_document() -> None:
    """A RAG_INGEST block rule (secrets here — not PII, so Stage 6 leaves it)
    now refuses the document before anything is embedded or stored."""
    from app.guardrails_v2.models import (
        GuardrailAction,
        GuardrailLayer,
        GuardrailRule,
        ViolationCategory,
    )

    client, store, embedder, cid = _app()
    guardrails_engine._rules.pop(_TENANT, None)
    try:
        guardrails_engine.add_rule(
            GuardrailRule(
                rule_id="r-rag-ingest-secrets",
                tenant_id=_TENANT,
                name="block-rag-ingest-secrets",
                rule_type="pii_detection",
                layers=[GuardrailLayer.RAG_INGEST],
                action=GuardrailAction.BLOCK,
                categories=[ViolationCategory.SECRETS],
            )
        )
        resp = client.post(
            "/knowledge/ingest",
            json={
                "collection_id": cid,
                "content": "Internal notes: token ghp_" + "a" * 36
                + " must never leave this repo or appear in the release notes.",
            },
            headers=_HDRS,
        )
    finally:
        guardrails_engine._rules.pop(_TENANT, None)
        guardrails_engine._violations.pop(_TENANT, None)
    assert resp.status_code == 422, resp.text
    assert "guardrail_blocked" in resp.json()["detail"]
    assert embedder.seen == [] and not store._data[(_TENANT, cid)].chunks


def test_ingest_file_redacts_pii() -> None:
    client, store, embedder, cid = _app()
    resp = client.post(
        "/knowledge/ingest/file",
        files={"file": ("record.txt", _BODY.encode(), "text/plain")},
        data={"collection_id": cid},
        headers=_HDRS,
    )
    assert resp.status_code == 201, resp.text
    assert all(_SSN not in t for t in embedder.seen)
    assert _SSN not in _stored_text(store, cid)


def test_ingest_openapi_redacts_pii_in_endpoint_text() -> None:
    client, store, embedder, cid = _app()
    import json

    spec = json.dumps(
        {
            "paths": {
                "/users": {
                    "get": {"summary": "List users", "description": f"Example SSN {_SSN}"}
                }
            }
        }
    )
    resp = client.post(
        "/knowledge/ingest/openapi",
        json={"collection_id": cid, "content": spec},
        headers=_HDRS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["endpoints_ingested"] == 1
    assert all(_SSN not in t for t in embedder.seen)
    assert _SSN not in _stored_text(store, cid)


def test_ingest_url_redacts_pii() -> None:
    client, store, embedder, cid = _app()
    with patch(
        "app.api.knowledge._fetch_url_content",
        new=AsyncMock(return_value=(_BODY, {"source_url": "https://example.com/x"})),
    ):
        resp = client.post(
            "/knowledge/ingest/url",
            json={"collection_id": cid, "url": "https://example.com/x"},
            headers=_HDRS,
        )
    assert resp.status_code == 201, resp.text
    assert all(_SSN not in t for t in embedder.seen)
    assert _SSN not in _stored_text(store, cid)


def test_legacy_ingestor_path_redacts_and_dedups_per_document() -> None:
    client, store, embedder, cid = _app()
    chunks = [
        {"content": _BODY, "source_doc_id": "doc-1", "source_type": "slack"},
        {"content": "An unrelated second message about release planning.", "source_doc_id": "doc-2"},
    ]
    with patch(
        "app.knowledge.ingestors.slack_ingestor.SlackIngestor.ingest_channel",
        new=AsyncMock(return_value=chunks),
    ):
        body = {"collection_id": cid, "channel_id": "C1", "token": "xoxb-test"}
        first = client.post("/knowledge/ingest/slack", json=body, headers=_HDRS)
        # Re-ingest: doc-1 and doc-2 are already indexed → nothing new.
        second = client.post("/knowledge/ingest/slack", json=body, headers=_HDRS)
    assert first.status_code == 202, first.text
    assert first.json()["chunks_ingested"] == 2
    assert second.json()["chunks_ingested"] == 0
    assert all(_SSN not in t for t in embedder.seen)
    assert _SSN not in _stored_text(store, cid)
    assert len(store._data[(_TENANT, cid)].chunks) == 2


def test_orchestrator_collection_documents_path_redacts_pii() -> None:
    """POST /collections/{id}/documents (IngestionOrchestrator) had no PII stage."""
    client, store, embedder, cid = _app()
    resp = client.post(
        f"/knowledge/collections/{cid}/documents",
        json={"content": _BODY, "content_type": "text", "in_memory_only": True},
        headers=_HDRS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["chunks_prepared"] >= 1
    assert all(_SSN not in t for t in embedder.seen)
    assert _SSN not in _stored_text(store, cid)
