"""Regression: direct /knowledge ingest routes bypassed the ingestion pipeline.

Despite the "LAW-01: single pipeline path" comment, ``/knowledge/ingest``,
``/ingest/file``, ``/ingest/url``, ``/ingest/openapi`` and the legacy
per-source ingestors (pdf/docx via ``_ingest_chunks_from_source``) parsed, chunked, embedded and persisted on their
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


def test_ingest_fails_closed_503_when_guardrail_rules_unavailable(monkeypatch: Any) -> None:
    """RAG_INGEST used to fail OPEN: an engine error (e.g. the tenant's rules
    could not be loaded) was logged and the document indexed unscreened."""
    from app.guardrails_v2.engine import GuardrailRulesUnavailableError

    async def _evaluate(*_a: Any, **_k: Any) -> Any:
        raise GuardrailRulesUnavailableError("rules could not be loaded")

    monkeypatch.setattr(guardrails_engine, "evaluate", _evaluate)
    client, store, embedder, cid = _app()
    resp = client.post(
        "/knowledge/ingest", json={"collection_id": cid, "content": _BODY}, headers=_HDRS
    )
    assert resp.status_code == 503, resp.text
    assert "guardrail" in resp.json()["detail"].lower()
    assert embedder.seen == [] and not store._data[(_TENANT, cid)].chunks


def test_collection_ingest_budget_refusal_is_429_not_storage_outage(monkeypatch: Any) -> None:
    """RAPTOR / agentic-chunking indexing is charged to the tenant; a refused
    charge fails the ingest honestly instead of "persistence is unavailable"."""
    from app.ingestion.orchestrator import IngestionOrchestrator
    from app.providers.guarded_completion import DecisionBudgetExceededError

    async def _refuse(*_a: Any, **_k: Any) -> Any:
        raise DecisionBudgetExceededError("tenant daily LLM budget exhausted")

    monkeypatch.setattr(IngestionOrchestrator, "ingest", _refuse)
    client, _store, _embedder, cid = _app()
    resp = client.post(
        f"/knowledge/collections/{cid}/documents", json={"content": _BODY}, headers=_HDRS
    )
    assert resp.status_code == 429, resp.text
    assert "budget" in resp.json()["detail"].lower()


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
    from app.ingestion.web_fetch import WebResource

    page = WebResource(url="https://example.com/x", final_url="https://example.com/x",
                       status=200, content_type="text/plain", data=_BODY.encode())
    with patch("app.api.knowledge._fetch_url_resource", new=AsyncMock(return_value=page)):
        resp = client.post(
            "/knowledge/ingest/url",
            json={"collection_id": cid, "url": "https://example.com/x"},
            headers=_HDRS,
        )
    assert resp.status_code == 201, resp.text
    assert all(_SSN not in t for t in embedder.seen)
    assert _SSN not in _stored_text(store, cid)


# The legacy GitHub / Confluence / Jira / Slack routes are durable jobs that run
# the connector through the IngestionPipeline itself (a04-F067-01); their PII
# redaction and re-ingest dedup are covered in
# tests/api/test_legacy_source_ingest_jobs.py.


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
