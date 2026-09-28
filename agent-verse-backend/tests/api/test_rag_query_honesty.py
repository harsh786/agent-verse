"""Regression: POST /rag/query fake success + mislabelled confidence.

* With no retrieval gateway it answered 200 with an empty "structurally valid"
  answer ("so strategy tests pass") — indistinguishable from "nothing found".
* ``confidence`` was the single best citation score, presented as an answer
  confidence, ignoring the gateway's calibrated ``retrieval_confidence``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rag_platform import router as rag_router
from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="rag-honest", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_rag_honest"
_HDRS = {"X-API-Key": _KEY}


def _client(gateway: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rag_router)
    if gateway is not None:
        app.state.retrieval_gateway = gateway
    return TestClient(app)


def _result(*, calibrated: float) -> RAGExecutionResult:
    return RAGExecutionResult(
        requested_strategy_id="hybrid",
        resolved_strategy_id=RAGStrategy.HYBRID,
        citations=[
            RAGCitation(citation_id="c1", chunk_id="k1", content="x", score=0.97, source="s"),
            RAGCitation(citation_id="c2", chunk_id="k2", content="y", score=0.2, source="s"),
        ],
        grounded=True,
        answer="a",
        retrieval_confidence=calibrated,
        low_confidence=calibrated < 0.35,
    )


def test_missing_gateway_is_503() -> None:
    resp = _client(None).post("/rag/query", json={"query": "q"}, headers=_HDRS)
    assert resp.status_code == 503


def test_unknown_strategy_still_422_without_gateway() -> None:
    resp = _client(None).post(
        "/rag/query", json={"query": "q", "strategy": "no-such-strategy"}, headers=_HDRS
    )
    assert resp.status_code == 422


def test_confidence_is_the_calibrated_retrieval_confidence() -> None:
    with patch(
        "app.api.rag_platform.RAGRetriever.retrieve",
        new=AsyncMock(return_value=_result(calibrated=0.31)),
    ):
        resp = _client(object()).post("/rag/query", json={"query": "q"}, headers=_HDRS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confidence"] == 0.31
    assert body["confidence_basis"] == "calibrated_retrieval"
    assert body["max_citation_score"] == 0.97
    assert body["low_confidence"] is True


def test_confidence_falls_back_to_labelled_max_citation_score() -> None:
    with patch(
        "app.api.rag_platform.RAGRetriever.retrieve",
        new=AsyncMock(return_value=_result(calibrated=0.0)),
    ):
        resp = _client(object()).post("/rag/query", json={"query": "q"}, headers=_HDRS)
    body = resp.json()
    assert body["confidence"] == 0.97
    assert body["confidence_basis"] == "max_citation_score"
