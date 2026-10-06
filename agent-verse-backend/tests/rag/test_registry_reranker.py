"""Model Registry driven reranking: per-provider adapters and ordered failover.

Adapters are exercised against an ``httpx.MockTransport`` (exact request URL,
auth and body; response parsing). The chain tests route every outbound call
through one mock transport and assert the order: preferred registry model →
next registry model → env-configured endpoint → local fallback.
"""

from __future__ import annotations

_ISOLATE_PROVIDER_ENV = True

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.rag_platform import hosted_reranker as hosted_mod
from app.rag_platform import registry_reranker as rr_mod
from app.rag_platform.hosted_reranker import HostedReranker, HostedRerankerError
from app.rag_platform.registry_reranker import (
    CohereReranker,
    FailoverReranker,
    NvidiaReranker,
    RerankTarget,
    VoyageReranker,
    reranker_chain_from_settings,
)

ENV_URL = "https://1.1.1.1/v1/rerank"


@dataclass
class _M:
    provider: str
    model_id: str
    extra: dict[str, Any] = field(default_factory=lambda: {"source": "override"})


class _Settings:
    rag_hosted_reranker_url = ""
    rag_hosted_reranker_api_key = ""
    rag_hosted_reranker_model = "env-rerank-model"
    rag_hosted_reranker_timeout_seconds = 5.0
    rag_hosted_reranker_allow_internal = False
    onprem_reranker_url = ""
    onprem_api_key = "EMPTY"


class _Recorder:
    """Mock transport: records each request, answers via per-URL handlers."""

    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content or b"{}")
        self.calls.append((url, body, dict(request.headers)))
        handler = self.handlers.get(url)
        if handler is None:
            return httpx.Response(404, json={"error": "no handler"})
        result = handler(body)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    @property
    def urls(self) -> list[str]:
        return [u for u, _, _ in self.calls]


def _client(rec: _Recorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(rec))


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch):
    """Send every native-adapter and env-endpoint call through one recorder."""

    def _install(handlers: dict[str, Any]) -> _Recorder:
        rec = _Recorder(handlers)
        monkeypatch.setattr(rr_mod, "public_async_client", lambda **_kw: _client(rec))
        monkeypatch.setattr(hosted_mod, "public_async_client", lambda **_kw: _client(rec))
        return rec

    return _install


# ── adapters: exact request / response shapes ────────────────────────────────


@pytest.mark.asyncio
async def test_nvidia_adapter_request_and_response_shape() -> None:
    rec = _Recorder({})
    rec.handlers[
        "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-3_2-nv-rerankqa-1b-v2/reranking"
    ] = lambda body: {"rankings": [{"index": 1, "logit": 4.5}, {"index": 0, "logit": -1.25}]}
    rr = NvidiaReranker(
        model="nvidia/llama-3.2-nv-rerankqa-1b-v2", api_key="test-nvidia-key", client=_client(rec)
    )
    pairs = await rr.rerank("what is x", ["doc a", "doc b"])

    assert pairs == [(1, 4.5), (0, -1.25)]
    url, body, headers = rec.calls[0]
    assert body == {
        "model": "nvidia/llama-3.2-nv-rerankqa-1b-v2",
        "query": {"text": "what is x"},
        "passages": [{"text": "doc a"}, {"text": "doc b"}],
        "truncate": "END",
    }
    assert headers["authorization"] == "Bearer test-nvidia-key"


def test_nvidia_url_keeps_slash_and_maps_dots() -> None:
    rr = NvidiaReranker(model="nvidia/nv-rerankqa-mistral-4b-v3", api_key="k")
    assert rr.url() == (
        "https://ai.api.nvidia.com/v1/retrieval/nvidia/nv-rerankqa-mistral-4b-v3/reranking"
    )


@pytest.mark.asyncio
async def test_voyage_adapter_request_and_response_shape() -> None:
    rec = _Recorder(
        {
            "https://api.voyageai.com/v1/rerank": lambda body: {
                "object": "list",
                "data": [{"index": 0, "relevance_score": 0.2}, {"index": 1, "relevance_score": 0.9}],
            }
        }
    )
    rr = VoyageReranker(model="rerank-2.5", api_key="test-voyage-key", client=_client(rec))
    pairs = await rr.rerank("q", ["a", "b"], top_k=2)

    assert pairs == [(1, 0.9), (0, 0.2)]
    _, body, headers = rec.calls[0]
    assert body == {"query": "q", "documents": ["a", "b"], "model": "rerank-2.5", "top_k": 2}
    assert headers["authorization"] == "Bearer test-voyage-key"


@pytest.mark.asyncio
async def test_cohere_adapter_request_and_response_shape() -> None:
    rec = _Recorder(
        {
            "https://api.cohere.com/v2/rerank": lambda body: {
                "results": [{"index": 1, "relevance_score": 0.7}, {"index": 0, "relevance_score": 0.1}]
            }
        }
    )
    rr = CohereReranker(model="rerank-v3.5", api_key="test-cohere-key", client=_client(rec))
    pairs = await rr.rerank("q", ["a", "b"], top_k=2)

    assert pairs == [(1, 0.7), (0, 0.1)]
    _, body, headers = rec.calls[0]
    assert body == {"model": "rerank-v3.5", "query": "q", "documents": ["a", "b"], "top_n": 2}
    assert headers["authorization"] == "Bearer test-cohere-key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={"error": "boom"}),
        # NVIDIA answers 404 for a model the account is not entitled to / retired.
        httpx.Response(404, json={"detail": "Function not found for account"}),
        httpx.Response(410, text="gone"),
        {"unexpected": []},
        {"rankings": [{"index": 9, "logit": 1.0}]},
        {"rankings": [{"index": 0, "logit": "high"}]},
    ],
)
async def test_adapter_errors_become_hosted_reranker_errors(answer: Any) -> None:
    url = "https://ai.api.nvidia.com/v1/retrieval/nvidia/r/reranking"
    rec = _Recorder({url: lambda body: answer})
    rr = NvidiaReranker(model="nvidia/r", api_key="test-nvidia-key", client=_client(rec))
    with pytest.raises(HostedRerankerError):
        await rr.rerank("q", ["a"])


@pytest.mark.asyncio
async def test_adapter_timeout_becomes_hosted_reranker_error() -> None:
    def _slow(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_slow))
    rr = VoyageReranker(model="rerank-2.5", api_key="test-voyage-key", client=client)
    with pytest.raises(HostedRerankerError, match="ReadTimeout"):
        await rr.rerank("q", ["a"])


# ── chain construction ───────────────────────────────────────────────────────


def test_chain_is_none_when_nothing_is_configured() -> None:
    assert reranker_chain_from_settings(_Settings(), models=[]) is None


def test_chain_skips_native_providers_without_credentials(monkeypatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    chain = reranker_chain_from_settings(
        _Settings(),
        models=[_M("nvidia", "nvidia/llama-3.2-nv-rerankqa-1b-v2"), _M("voyage", "rerank-2.5")],
    )
    assert chain is not None
    assert [t.label for t in chain.targets] == ["voyage/rerank-2.5"]


def test_chain_routes_onprem_custom_and_env_seeded_models(monkeypatch) -> None:
    s = _Settings()
    s.rag_hosted_reranker_url = ENV_URL
    s.onprem_reranker_url = "http://192.168.1.20:30083/v1/rerank"
    chain = reranker_chain_from_settings(
        s,
        models=[
            _M("onprem", "Qwen/Qwen3-Reranker-0.6B"),
            _M("custom", "my-reranker"),
            _M("openai", "env-rerank-model", {"source": "env"}),
        ],
    )
    assert chain is not None
    labels = [t.label for t in chain.targets]
    # The env endpoint's own model is not appended twice.
    assert labels == [
        "onprem/Qwen/Qwen3-Reranker-0.6B",
        "endpoint/my-reranker",
        "endpoint/env-rerank-model",
    ]
    onprem = chain.targets[0].reranker
    assert isinstance(onprem, HostedReranker)
    assert onprem._allow_internal is True


def test_chain_uses_the_registry_preference_order(monkeypatch) -> None:
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    model_registry.clear_configured()
    try:
        for provider, mid, cost in (
            ("voyage", "rerank-2.5-lite", 0.00002),
            ("nvidia", "nvidia/llama-3.2-nv-rerankqa-1b-v2", 0.00003),
        ):
            model_registry.register_configured(
                ModelEndpoint(
                    provider=provider, model_id=mid, display_name=mid,
                    capabilities=[ModelCapability.RERANK], cost_per_1k_input=cost,
                    extra={"source": "override"},
                )
            )
        model_registry.set_preferences({"rerank": ["nvidia/nvidia/llama-3.2-nv-rerankqa-1b-v2"]})
        chain = reranker_chain_from_settings(_Settings())
        assert chain is not None
        assert [t.label for t in chain.targets] == [
            "nvidia/nvidia/llama-3.2-nv-rerankqa-1b-v2",
            "voyage/rerank-2.5-lite",
        ]
    finally:
        model_registry.clear_configured()
        model_registry.set_preferences({})


# ── failover order ───────────────────────────────────────────────────────────

_NV_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-3_2-nv-rerankqa-1b-v2/reranking"
_VOYAGE_URL = "https://api.voyageai.com/v1/rerank"


def _down(_body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(503, json={"error": "unavailable"})


@pytest.mark.asyncio
async def test_preferred_fails_over_to_next_registry_model(monkeypatch, route, caplog) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    rec = route(
        {
            _NV_URL: _down,
            _VOYAGE_URL: lambda body: {
                "data": [{"index": 1, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.3}]
            },
        }
    )
    chain = reranker_chain_from_settings(
        _Settings(),
        models=[_M("nvidia", "nvidia/llama-3.2-nv-rerankqa-1b-v2"), _M("voyage", "rerank-2.5")],
    )
    assert chain is not None
    with caplog.at_level(logging.WARNING, logger=rr_mod.__name__):
        pairs = await chain.rerank("q", ["a", "b"])

    assert pairs == [(1, 0.8), (0, 0.3)]  # Voyage's full answer, no NVIDIA scores
    assert rec.urls == [_NV_URL, _VOYAGE_URL]
    assert chain.last_model == "voyage/rerank-2.5"
    assert any(
        "rerank_failover from=nvidia/nvidia/llama-3.2-nv-rerankqa-1b-v2 to=voyage/rerank-2.5"
        in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_all_registry_models_fail_then_env_endpoint_answers(monkeypatch, route) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    rec = route(
        {
            _NV_URL: _down,
            _VOYAGE_URL: lambda body: {"data": [{"index": 0}]},  # malformed: no score
            ENV_URL: lambda body: {
                "results": [{"index": 0, "relevance_score": 0.6}, {"index": 1, "relevance_score": 0.4}]
            },
        }
    )
    s = _Settings()
    s.rag_hosted_reranker_url = ENV_URL
    chain = reranker_chain_from_settings(
        s,
        models=[_M("nvidia", "nvidia/llama-3.2-nv-rerankqa-1b-v2"), _M("voyage", "rerank-2.5")],
    )
    assert chain is not None
    pairs = await chain.rerank("q", ["a", "b"])

    assert pairs == [(0, 0.6), (1, 0.4)]
    assert rec.urls == [_NV_URL, _VOYAGE_URL, ENV_URL]
    assert rec.calls[-1][1]["model"] == "env-rerank-model"


@pytest.mark.asyncio
async def test_partial_ranking_fails_over_instead_of_mixing(monkeypatch, route) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    route(
        {
            _VOYAGE_URL: lambda body: {"data": [{"index": 0, "relevance_score": 0.9}]},
            ENV_URL: lambda body: {
                "results": [{"index": 1, "relevance_score": 0.5}, {"index": 0, "relevance_score": 0.2}]
            },
        }
    )
    s = _Settings()
    s.rag_hosted_reranker_url = ENV_URL
    chain = reranker_chain_from_settings(s, models=[_M("voyage", "rerank-2.5")])
    assert chain is not None
    assert await chain.rerank("q", ["a", "b"]) == [(1, 0.5), (0, 0.2)]


@pytest.mark.asyncio
async def test_every_target_failing_raises_for_the_local_fallback() -> None:
    class _Broken:
        async def rerank(self, query: str, documents: list[str], top_k: int | None = None):
            raise RuntimeError("down")

    chain = FailoverReranker(
        [RerankTarget("a", ("a", "m"), _Broken()), RerankTarget("b", ("b", "m"), _Broken())]
    )
    with pytest.raises(HostedRerankerError, match="every reranker failed"):
        await chain.rerank("q", ["x"])


@pytest.mark.asyncio
async def test_policy_falls_back_to_local_when_whole_chain_fails(monkeypatch, route) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    rec = route({_NV_URL: _down, ENV_URL: _down})
    s = _Settings()
    s.rag_hosted_reranker_url = ENV_URL
    monkeypatch.setattr("app.core.config.get_settings", lambda: s)
    monkeypatch.setattr(
        rr_mod, "_registry_models", lambda: [_M("nvidia", "nvidia/llama-3.2-nv-rerankqa-1b-v2")]
    )
    chunks = [
        {"chunk_id": "c0", "content": "unrelated text", "score": 0.5},
        {"chunk_id": "c1", "content": "alpha beta", "score": 0.4},
    ]
    policy = RerankPolicy(strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0)
    out = await policy.rerank_async(chunks, "alpha", strategy=RerankStrategy.HOSTED)

    assert rec.urls == [_NV_URL, ENV_URL]
    assert {c["chunk_id"] for c in out} == {"c0", "c1"}  # local TF-IDF, nothing dropped
    assert all("hosted_rerank_score" not in c for c in out)


@pytest.mark.asyncio
async def test_policy_uses_the_registry_model_and_records_it(monkeypatch, route) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "test-voyage-key")
    route(
        {
            _VOYAGE_URL: lambda body: {
                "data": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]
            }
        }
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: _Settings())
    monkeypatch.setattr(rr_mod, "_registry_models", lambda: [_M("voyage", "rerank-2.5")])
    chunks = [
        {"chunk_id": "c0", "content": "a", "score": 0.5},
        {"chunk_id": "c1", "content": "b", "score": 0.4},
    ]
    policy = RerankPolicy(strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0)
    out = await policy.rerank_async(chunks, "q", strategy=RerankStrategy.HOSTED)

    assert [c["chunk_id"] for c in out] == ["c1", "c0"]
    assert out[0]["hosted_rerank_model"] == "voyage/rerank-2.5"
