"""P2-4: ``top_k`` above the retrieval contract's maximum is a 422, never a 503.

``GET /knowledge/search`` accepted ``top_k`` up to 100, but the gateway's
``RAGExecutionRequest.top_k`` is capped at 20; the resulting ValidationError
fell through the error mapper as 503 "Retrieval service is unavailable".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.rag.contracts import (
    MAX_RAG_TOP_K,
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGStrategy,
)
from tests.api.test_knowledge_api import _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}


class _ContractGateway:
    """Builds the real request contract, as the production gateway does."""

    def __init__(self) -> None:
        self.top_ks: list[int] = []

    async def execute(
        self,
        tenant_ctx: Any,
        *,
        collection_id: str,
        query: str,
        strategy_id: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> RAGExecutionResult:
        RAGExecutionRequest(
            tenant_id=tenant_ctx.tenant_id,
            query=query,
            requested_strategy_id=strategy_id,
            collection_id=collection_id,
            top_k=top_k,
            filters=filters,
        )
        self.top_ks.append(top_k)
        return RAGExecutionResult(
            requested_strategy_id=strategy_id,
            resolved_strategy_id=RAGStrategy.HYBRID,
            citations=[
                RAGCitation(citation_id="c1", chunk_id="k1", content="x", score=0.5, source="s")
            ],
            grounded=True,
        )


def _client() -> tuple[TestClient, _ContractGateway]:
    app = _make_app()
    gateway = _ContractGateway()
    app.state.retrieval_gateway = gateway
    return TestClient(app, raise_server_exceptions=False), gateway


def test_contract_maximum_is_twenty() -> None:
    assert MAX_RAG_TOP_K == 20


@pytest.mark.parametrize("param", ["top_k", "limit"])
@pytest.mark.parametrize("value", [21, 50, 100])
def test_search_top_k_above_the_maximum_is_422_with_the_range(param: str, value: int) -> None:
    client, gateway = _client()
    resp = client.get(f"/knowledge/search?q=x&collection_id=c&{param}={value}", headers=_H)
    assert resp.status_code == 422, resp.text
    assert "20" in resp.text
    assert gateway.top_ks == []


@pytest.mark.parametrize("value", [0, -3])
def test_search_top_k_below_one_is_422(value: int) -> None:
    client, _ = _client()
    resp = client.get(f"/knowledge/search?q=x&collection_id=c&top_k={value}", headers=_H)
    assert resp.status_code == 422


def test_search_top_k_at_the_maximum_is_served() -> None:
    client, gateway = _client()
    resp = client.get("/knowledge/search?q=x&collection_id=c&top_k=20", headers=_H)
    assert resp.status_code == 200, resp.text
    assert gateway.top_ks == [20]


@pytest.mark.parametrize("value", [0, 21, 100, "many"])
def test_federated_search_top_k_out_of_range_is_422(value: object) -> None:
    client, gateway = _client()
    resp = client.post(
        "/knowledge/search/federated",
        json={"query": "x", "collection_ids": ["c"], "top_k": value},
        headers=_H,
    )
    assert resp.status_code == 422, resp.text
    assert "20" in resp.text
    assert gateway.top_ks == []


@pytest.mark.parametrize("module", ["app.api.knowledge", "app.api.rag_platform"])
def test_a_request_contract_violation_maps_to_422_not_503(module: str) -> None:
    import importlib

    mapper = importlib.import_module(module)._raise_retrieval_http_error
    try:
        RAGExecutionRequest(tenant_id="t", query="q", requested_strategy_id="hybrid", top_k=99)
    except ValidationError as exc:
        with pytest.raises(HTTPException) as info:
            mapper(exc)
    assert info.value.status_code == 422
    assert "20" in str(info.value.detail)
