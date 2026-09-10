"""Hosted reranker provider — managed Cohere-compatible /v1/rerank.

Covers the client (parse/order, SSRF block, error handling) and its wiring into
RerankPolicy's async path (reorders by relevance score, honest fallback).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.rag_platform.hosted_reranker import (
    HostedReranker,
    HostedRerankerError,
    hosted_reranker_from_settings,
    is_hosted_reranker_configured,
)


class _FakeResp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self) -> Any:
        return self._payload


def _client_returning(payload: Any, status: int = 200) -> Any:
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResp(payload, status))
    return client


@pytest.mark.asyncio
async def test_rerank_parses_and_orders_by_score() -> None:
    # Endpoint says doc index 2 is most relevant, then 0, then 1.
    payload = {
        "results": [
            {"index": 0, "relevance_score": 0.4},
            {"index": 1, "relevance_score": 0.1},
            {"index": 2, "relevance_score": 0.9},
        ]
    }
    rr = HostedReranker(url="https://1.1.1.1/v1/rerank", client=_client_returning(payload))
    pairs = await rr.rerank("q", ["a", "b", "c"])
    assert pairs == [(2, 0.9), (0, 0.4), (1, 0.1)]


@pytest.mark.asyncio
async def test_rerank_blocks_internal_url() -> None:
    rr = HostedReranker(url="http://169.254.169.254/v1/rerank", client=_client_returning({"results": []}))
    with pytest.raises(HostedRerankerError, match="blocked"):
        await rr.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_rerank_rejects_malformed_response() -> None:
    rr = HostedReranker(url="https://1.1.1.1/v1/rerank", client=_client_returning({"nope": 1}))
    with pytest.raises(HostedRerankerError, match="missing 'results'"):
        await rr.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_rerank_rejects_out_of_range_index() -> None:
    payload = {"results": [{"index": 5, "relevance_score": 0.9}]}
    rr = HostedReranker(url="https://1.1.1.1/v1/rerank", client=_client_returning(payload))
    with pytest.raises(HostedRerankerError, match="index is invalid"):
        await rr.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_rerank_http_error_becomes_hosted_error() -> None:
    rr = HostedReranker(
        url="https://1.1.1.1/v1/rerank", client=_client_returning({"results": []}, status=500)
    )
    with pytest.raises(HostedRerankerError, match="request failed"):
        await rr.rerank("q", ["a"])


def test_empty_url_rejected() -> None:
    with pytest.raises(ValueError, match="url is required"):
        HostedReranker(url="")


def test_factory_and_is_configured() -> None:
    class S:
        rag_hosted_reranker_url = ""

    assert hosted_reranker_from_settings(S()) is None
    assert is_hosted_reranker_configured(S()) is False

    class S2:
        rag_hosted_reranker_url = "https://1.1.1.1/v1/rerank"
        rag_hosted_reranker_api_key = "k"
        rag_hosted_reranker_model = "rerank-english-v3.0"
        rag_hosted_reranker_timeout_seconds = 5.0

    rr = hosted_reranker_from_settings(S2())
    assert isinstance(rr, HostedReranker)
    assert is_hosted_reranker_configured(S2()) is True


# ── RerankPolicy wiring ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_hosted_reorders_by_relevance(monkeypatch: Any) -> None:
    chunks = [
        {"chunk_id": "c0", "content": "alpha", "score": 0.5},
        {"chunk_id": "c1", "content": "beta", "score": 0.9},
        {"chunk_id": "c2", "content": "gamma", "score": 0.1},
    ]
    payload = {
        "results": [
            {"index": 0, "relevance_score": 0.2},
            {"index": 1, "relevance_score": 0.3},
            {"index": 2, "relevance_score": 0.95},  # gamma wins per the endpoint
        ]
    }

    fake = HostedReranker(url="https://1.1.1.1/v1/rerank", client=_client_returning(payload))
    monkeypatch.setattr(
        "app.rag_platform.hosted_reranker.hosted_reranker_from_settings", lambda _s: fake
    )

    policy = RerankPolicy(strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0)
    out = await policy.rerank_async(chunks, "query", strategy=RerankStrategy.HOSTED)
    assert [c["chunk_id"] for c in out] == ["c2", "c1", "c0"]
    assert out[0]["hosted_rerank_score"] == 0.95
    assert out[0]["pre_rerank_score"] == 0.1  # gamma's original score preserved


@pytest.mark.asyncio
async def test_policy_hosted_falls_back_when_unconfigured(monkeypatch: Any) -> None:
    chunks = [{"chunk_id": "c0", "content": "alpha beta", "score": 0.5}]
    monkeypatch.setattr(
        "app.rag_platform.hosted_reranker.hosted_reranker_from_settings", lambda _s: None
    )
    policy = RerankPolicy(strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0)
    out = await policy.rerank_async(chunks, "alpha", strategy=RerankStrategy.HOSTED)
    # Falls back to the local TF-IDF path — never drops the result.
    assert len(out) == 1
    assert out[0]["chunk_id"] == "c0"
