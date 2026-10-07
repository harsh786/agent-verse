"""Reranking follows the Model Registry (RERANK resolver).

``auto`` — the default ``RAG_DEFAULT_RERANK_STRATEGY`` — resolves the reranker
through :func:`app.ai_router.resolve.resolve_reranker`: the registry ``rerank``
preference order → the env/settings hosted endpoint → the local cross-encoder →
score order flagged ``rerank_degraded``.

The registry reranker is exercised against a REAL local HTTP ``/v1/rerank``
server (a vLLM Qwen3-Reranker stand-in) started by the test: the app's own HTTP
client, SSRF policy, request shape and response parsing all run.
"""

from __future__ import annotations

_ISOLATE_PROVIDER_ENV = True

import json
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import ModelRegistry, model_registry
from app.ai_router.resolve import ModelNotConfiguredError, resolve_reranker
from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.rag import cross_encoder
from app.rag.cross_encoder import CrossEncoderReranker
from app.rag.engine import RetrievalResult
from app.rag.rerank_stage import apply_default_rerank

QWEN = "Qwen/Qwen3-Reranker-0.6B"
QWEN_KEY = f"onprem/{QWEN}"
DEFAULT_CE = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_RERANK_ENV = (
    "RAG_HOSTED_RERANKER_URL",
    "RAG_HOSTED_RERANKER_MODEL",
    "ONPREM_RERANKER_URL",
    "ONPREM_RERANKER_MODEL",
    "RAG_CROSS_ENCODER_MODEL",
    "RAG_DEFAULT_RERANK_STRATEGY",
)


# ── a real /v1/rerank server ─────────────────────────────────────────────────


class RerankServer:
    """Cohere/vLLM-shaped ``POST /v1/rerank`` on 127.0.0.1 (a real socket).

    Scores a document by how many query words it contains; records every request.
    """

    def __init__(self, *, delay: float = 0.0, status: int = 200) -> None:
        self.delay = delay
        self.status = status
        self.requests: list[dict[str, Any]] = []
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append({"path": self.path, "body": body})
                if server.delay:
                    time.sleep(server.delay)
                if self.path != "/v1/rerank" or server.status != 200:
                    self._send(server.status if server.status != 200 else 404, {"error": "x"})
                    return
                words = str(body.get("query", "")).lower().split()
                docs = [str(d).lower() for d in body.get("documents", [])]
                results = [
                    {"index": i, "relevance_score": sum(w in d for w in words) / max(len(words), 1)}
                    for i, d in enumerate(docs)
                ]
                results.sort(key=lambda r: r["relevance_score"], reverse=True)
                self._send(200, {"model": body.get("model"), "results": results})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                del args

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def rerank_server() -> Iterator[Any]:
    servers: list[RerankServer] = []

    def _start(**kwargs: Any) -> RerankServer:
        server = RerankServer(**kwargs)
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.close()


@pytest.fixture(autouse=True)
def _clean_rerank_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    for var in _RERANK_ENV:
        monkeypatch.delenv(var, raising=False)
    # The deployment default (owner decision): model endpoints may be private hosts
    # — the stand-in reranker listens on 127.0.0.1.
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModelRegistry]:
    """The process registry, emptied for the test and restored after it."""
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    saved = dict(model_registry._configured)
    saved_prefs = {k: list(v) for k, v in model_registry._preferences.items()}
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield model_registry
    model_registry.clear_configured()
    for endpoint in saved.values():
        model_registry.register_configured(endpoint)
    model_registry.set_preferences(saved_prefs)


def _qwen(base_url: str | None) -> ModelEndpoint:
    return ModelEndpoint(
        provider="onprem",
        model_id=QWEN,
        display_name=QWEN,
        capabilities=[ModelCapability.RERANK],
        base_url=base_url,
        extra={"source": "override", "origin": "manual"},
    )


def _register_qwen(reg: ModelRegistry, base_url: str, *, preferred: bool = True) -> None:
    reg.register_configured(_qwen(base_url))
    if preferred:
        reg.set_preferences({"rerank": [QWEN_KEY]})


def _settings(**extra: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "rag_default_rerank_enabled": True,
        "rag_default_rerank_strategy": "auto",
        "rag_rerank_warmup_wait_seconds": 0.05,
        "rag_rerank_budget_ms": 2500,
        "rag_rerank_max_candidates": 30,
        "rag_hosted_reranker_url": "",
        "rag_hosted_reranker_model": "env-rerank-model",
        "rag_hosted_reranker_api_key": "",
        "rag_hosted_reranker_timeout_seconds": 5.0,
        "rag_hosted_reranker_allow_internal": False,
        "onprem_reranker_url": "",
        "onprem_reranker_model": "",
        "onprem_api_key": "EMPTY",
        "rag_cross_encoder_model": DEFAULT_CE,
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _results() -> list[RetrievalResult]:
    # Retrieval order c0, c1, c2; the query matches c2 best, then c1.
    contents = ["unrelated passage", "alpha only", "alpha beta gamma"]
    return [
        RetrievalResult(
            chunk_id=f"c{i}", content=text, score=1.0 - i * 0.1, source_metadata={}
        )
        for i, text in enumerate(contents)
    ]


def _chunks() -> list[dict[str, Any]]:
    return [
        {"chunk_id": r.chunk_id, "content": r.content, "score": r.score} for r in _results()
    ]


class _WordModel:
    """Local cross-encoder stand-in: prefers passages containing "only", so its
    order is distinguishable from the hosted server's."""

    def predict(self, pairs: list[tuple[str, str]], *, batch_size: int) -> list[float]:
        del batch_size
        return [10.0 if "only" in doc else -10.0 for _q, doc in pairs]


@pytest.fixture
def warm_local_cross_encoder(monkeypatch: pytest.MonkeyPatch) -> Iterator[CrossEncoderReranker]:
    reranker = CrossEncoderReranker(model_loader=_WordModel)
    reranker.start_warmup().result(timeout=5)
    monkeypatch.setattr(cross_encoder, "_default_reranker", reranker)
    yield reranker
    reranker.close_sync()


# ── resolver order ───────────────────────────────────────────────────────────


def test_registry_rerank_model_comes_first(registry: ModelRegistry) -> None:
    s = _settings(rag_hosted_reranker_url="https://1.1.1.1/v1/rerank")
    choice = resolve_reranker(
        s, models=[_qwen("http://10.0.0.5:30083/v1")], preferences=[QWEN_KEY], local_available=True
    )

    assert choice.tier == "hosted"
    assert choice.resolution.model == QWEN
    assert choice.resolution.source == "registry_preference"
    assert choice.resolution.base_url == "http://10.0.0.5:30083/v1"
    # Registry model, then the env endpoint, then the local cross-encoder.
    assert choice.labels == (QWEN_KEY, "endpoint/env-rerank-model")
    assert choice.resolution.fallbacks == ("endpoint/env-rerank-model", f"local/{DEFAULT_CE}")
    assert choice.reranker is not None
    head = choice.reranker.targets[0]
    # Served at the model's OWN registry endpoint.
    assert head.key == ("http://10.0.0.5:30083/v1/rerank", QWEN)


def test_registry_model_without_preference_is_cheapest_source(registry: ModelRegistry) -> None:
    choice = resolve_reranker(
        _settings(), models=[_qwen("http://10.0.0.5:30083/v1")], preferences=[],
        local_available=False,
    )
    assert choice.tier == "hosted"
    assert choice.resolution.source == "registry_cheapest"
    assert choice.local_available is False
    assert choice.resolution.fallbacks == ()


def test_only_env_endpoint_is_an_env_pin(registry: ModelRegistry) -> None:
    s = _settings(rag_hosted_reranker_url="https://1.1.1.1/v1/rerank")
    choice = resolve_reranker(s, models=[], preferences=[], local_available=True)

    assert choice.tier == "hosted"
    assert choice.resolution.source == "env_pin"
    assert choice.resolution.model == "env-rerank-model"
    assert choice.resolution.base_url == "https://1.1.1.1/v1/rerank"
    assert choice.resolution.fallbacks == (f"local/{DEFAULT_CE}",)


def test_only_onprem_settings_endpoint_is_an_env_pin(registry: ModelRegistry) -> None:
    s = _settings(onprem_reranker_url="http://10.0.0.5:30083/v1/rerank", onprem_reranker_model=QWEN)
    choice = resolve_reranker(s, models=[], preferences=[], local_available=False)

    assert choice.tier == "hosted"
    assert choice.resolution.source == "env_pin"
    assert choice.resolution.model == QWEN
    assert choice.labels == (QWEN_KEY,)


def test_only_local_cross_encoder(registry: ModelRegistry) -> None:
    s = _settings(rag_cross_encoder_model="BAAI/bge-reranker-v2-m3")
    choice = resolve_reranker(s, models=[], preferences=[], local_available=True)

    assert choice.tier == "local"
    assert choice.reranker is None
    assert choice.resolution.source == "local_default"
    assert choice.resolution.model == "BAAI/bge-reranker-v2-m3"


def test_nothing_configured_is_degraded_or_an_honest_error(registry: ModelRegistry) -> None:
    choice = resolve_reranker(_settings(), models=[], preferences=[], local_available=False)
    assert choice.tier == "degraded"
    assert choice.resolution.source == "degraded"
    assert choice.resolution.model == ""

    with pytest.raises(ModelNotConfiguredError) as err:
        resolve_reranker(
            _settings(), models=[], preferences=[], local_available=False, strict=True
        )
    assert err.value.capability == "rerank"
    assert "Model Registry" in str(err.value)


# ── auto uses the registry reranker (real HTTP) ──────────────────────────────


@pytest.mark.asyncio
async def test_auto_default_path_uses_the_registry_reranker(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server()
    _register_qwen(registry, server.base_url)

    out = await apply_default_rerank(
        _results(), query="alpha beta", query_embedding=None, settings=_settings()
    )

    assert [r.chunk_id for r in out] == ["c2", "c1", "c0"]
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/rerank"
    assert request["body"]["model"] == QWEN
    assert request["body"]["documents"] == ["unrelated passage", "alpha only", "alpha beta gamma"]
    for result in out:
        assert result.source_metadata["rerank_strategy"] == "hosted"
        assert "rerank_degraded" not in result.source_metadata
    assert out[0].score == pytest.approx(1.0)
    assert out[0].source_metadata["pre_rerank_score"] == pytest.approx(0.8)


def test_auto_default_setting_is_auto() -> None:
    from app.core.config import Settings

    assert Settings().rag_default_rerank_strategy == "auto"


@pytest.mark.asyncio
async def test_auto_policy_async_records_the_resolution(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server()
    _register_qwen(registry, server.base_url)
    policy = RerankPolicy(strategy=RerankStrategy.AUTO, deduplicate=False, min_score=0.0)

    out = await policy.rerank_async(_chunks(), "alpha beta", strategy=RerankStrategy.AUTO)

    assert [c["chunk_id"] for c in out] == ["c2", "c1", "c0"]
    assert out[0]["hosted_rerank_model"] == QWEN_KEY
    assert policy.last_strategy_used is RerankStrategy.HOSTED
    assert policy.last_resolution is not None
    assert policy.last_resolution.model == QWEN
    assert policy.last_resolution.source == "registry_preference"
    assert policy.last_reason == "auto:registry_preference"


@pytest.mark.asyncio
async def test_hosted_failure_falls_through_to_the_local_tier_flagged(
    registry: ModelRegistry, rerank_server: Any, warm_local_cross_encoder: Any
) -> None:
    server = rerank_server(status=500)
    _register_qwen(registry, server.base_url)

    out = await apply_default_rerank(
        _results(), query="alpha beta", query_embedding=None, settings=_settings()
    )

    assert len(server.requests) == 1
    # The local stand-in prefers "alpha only" (c1).
    assert out[0].chunk_id == "c1"
    for result in out:
        assert result.source_metadata["rerank_strategy"] == "cross_encoder"
        assert result.source_metadata["rerank_degraded"] == "hosted_reranker_error"


@pytest.mark.asyncio
async def test_hosted_failure_without_local_tier_is_score_order_flagged(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server(status=503)
    _register_qwen(registry, server.base_url)
    choice = resolve_reranker(_settings(), local_available=False)
    policy = RerankPolicy(strategy=RerankStrategy.AUTO, deduplicate=False, min_score=0.0)

    chunks = list(reversed(_chunks()))
    out = await policy.rerank_async(
        chunks, "alpha beta", strategy=RerankStrategy.AUTO, resolution=choice
    )

    assert [c["chunk_id"] for c in out] == ["c0", "c1", "c2"]  # score order
    assert policy.last_strategy_used is RerankStrategy.SCORE
    assert policy.last_degraded_reason == "hosted_reranker_error"


@pytest.mark.asyncio
async def test_nothing_configured_auto_flags_degraded_score_order(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cross_encoder, "_sentence_transformers_installed", lambda: False)
    results = list(reversed(_results()))

    out = await apply_default_rerank(
        results, query="alpha beta", query_embedding=None, settings=_settings()
    )

    assert [r.chunk_id for r in out] == ["c0", "c1", "c2"]
    for result in out:
        assert result.source_metadata["rerank_strategy"] == "score"
        assert result.source_metadata["rerank_degraded"] == "no_reranker_configured"


# ── the hosted tier respects the rerank budget ───────────────────────────────


@pytest.mark.asyncio
async def test_auto_hosted_answer_past_the_budget_is_skipped_in_time(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server(delay=1.5)
    _register_qwen(registry, server.base_url)

    started = time.monotonic()
    out = await apply_default_rerank(
        list(reversed(_results())),
        query="alpha beta",
        query_embedding=None,
        settings=_settings(rag_rerank_budget_ms=200),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0  # never waits for the slow endpoint past the budget
    assert [r.chunk_id for r in out] == ["c0", "c1", "c2"]  # ``auto``: score order
    for result in out:
        assert result.source_metadata["rerank_skipped"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_explicit_hosted_respects_the_budget_too(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server(delay=1.5)
    _register_qwen(registry, server.base_url)
    policy = RerankPolicy(strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0)

    started = time.monotonic()
    out = await policy.rerank_async(
        _chunks(), "alpha beta", strategy=RerankStrategy.HOSTED, budget_seconds=0.2
    )

    assert time.monotonic() - started < 1.0
    assert [c["chunk_id"] for c in out] == ["c0", "c1", "c2"]  # retrieval order
    assert policy.last_skipped_reason == "budget_exceeded"


# ── the synchronous path never silently turns hosted into score order ────────


def test_sync_auto_runs_the_registry_reranker_off_the_event_loop(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server()
    _register_qwen(registry, server.base_url)
    policy = RerankPolicy(
        strategy=RerankStrategy.AUTO,
        deduplicate=False,
        min_score=0.0,
        max_per_source=0,
        calibration_method=None,
    )

    out = policy.rerank(_chunks(), "alpha beta")

    assert [c["chunk_id"] for c in out] == ["c2", "c1", "c0"]
    assert len(server.requests) == 1
    assert policy.last_strategy_used is RerankStrategy.HOSTED
    assert policy.last_degraded_reason is None
    assert out[0]["hosted_rerank_model"] == QWEN_KEY


@pytest.mark.asyncio
async def test_sync_auto_on_an_event_loop_says_it_could_not_call_hosted(
    registry: ModelRegistry, rerank_server: Any, warm_local_cross_encoder: Any
) -> None:
    server = rerank_server()
    _register_qwen(registry, server.base_url)
    policy = RerankPolicy(
        strategy=RerankStrategy.AUTO, deduplicate=False, min_score=0.0, max_per_source=0
    )

    out = policy.rerank(_chunks(), "alpha beta")

    assert server.requests == []  # no blocking HTTP on the event loop
    assert policy.last_degraded_reason == "hosted_reranker_needs_async"
    assert policy.last_strategy_used is RerankStrategy.CROSS_ENCODER
    assert out[0]["chunk_id"] == "c1"  # the local tier ranked


@pytest.mark.asyncio
async def test_sync_explicit_hosted_on_an_event_loop_degrades_to_tfidf_flagged(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server()
    _register_qwen(registry, server.base_url)
    policy = RerankPolicy(strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0)

    out = policy.rerank(_chunks(), "alpha beta")

    assert server.requests == []
    assert policy.last_strategy_used is RerankStrategy.TFIDF
    assert policy.last_degraded_reason == "hosted_reranker_needs_async"
    assert {c["chunk_id"] for c in out} == {"c0", "c1", "c2"}


def test_sync_explicit_hosted_off_the_loop_calls_the_endpoint(
    registry: ModelRegistry, rerank_server: Any
) -> None:
    server = rerank_server()
    _register_qwen(registry, server.base_url)
    policy = RerankPolicy(
        strategy=RerankStrategy.HOSTED, deduplicate=False, min_score=0.0, max_per_source=0
    )

    out = policy.rerank(_chunks(), "alpha beta")

    assert [c["chunk_id"] for c in out] == ["c2", "c1", "c0"]
    assert policy.last_strategy_used is RerankStrategy.HOSTED


# ── RAG_CROSS_ENCODER_MODEL ──────────────────────────────────────────────────


def test_cross_encoder_model_setting_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings, get_settings

    assert Settings().rag_cross_encoder_model == DEFAULT_CE
    assert cross_encoder.configured_cross_encoder_model() == DEFAULT_CE
    monkeypatch.setenv("RAG_CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3")
    get_settings.cache_clear()
    assert get_settings().rag_cross_encoder_model == "BAAI/bge-reranker-v2-m3"
    assert cross_encoder.configured_cross_encoder_model() == "BAAI/bge-reranker-v2-m3"
    # A hand-built settings object without the field keeps the default.
    assert cross_encoder.configured_cross_encoder_model(SimpleNamespace()) == DEFAULT_CE


def test_local_cross_encoder_loads_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    created: list[str] = []

    class _RecordingCrossEncoder:
        def __init__(self, name: str, **kwargs: Any) -> None:
            del kwargs
            created.append(name)

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=_RecordingCrossEncoder)
    )
    monkeypatch.setattr(cross_encoder, "configure_torch_threads", lambda *a, **k: None)
    monkeypatch.setenv("RAG_CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3")
    get_settings.cache_clear()

    cross_encoder._load_cross_encoder()

    assert created == ["BAAI/bge-reranker-v2-m3"]


# ── seeding the registry from settings ───────────────────────────────────────


def test_seeder_registers_the_onprem_reranker_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai_router.seeder import seed_registry_from_config

    monkeypatch.setenv("ONPREM_RERANKER_URL", "http://10.0.0.5:30083/v1/rerank")
    monkeypatch.setenv("ONPREM_RERANKER_MODEL", QWEN)
    reg = ModelRegistry()
    seed_registry_from_config(reg)

    seeded = reg.get_configured("onprem", QWEN)
    assert seeded is not None
    assert ModelCapability.RERANK in seeded.capabilities
    assert (seeded.extra or {}).get("source") == "env"
    # And it resolves to the on-prem endpoint as a deployment (env) reranker.
    choice = resolve_reranker(
        _settings(onprem_reranker_url="http://10.0.0.5:30083/v1/rerank"),
        models=[seeded],
        preferences=[],
        local_available=False,
    )
    assert choice.resolution.source == "env_pin"
    assert choice.labels == (QWEN_KEY,)


def test_seeder_registers_the_settings_hosted_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app copies the on-prem reranker into Settings (not os.environ)."""
    from app.ai_router.seeder import seed_registry_from_config
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "rag_hosted_reranker_url", "https://1.1.1.1/v1/rerank")
    monkeypatch.setattr(s, "rag_hosted_reranker_model", "rerank-2.5")
    reg = ModelRegistry()
    seed_registry_from_config(reg)

    seeded = [m for m in reg.list_configured(ModelCapability.RERANK) if m.model_id == "rerank-2.5"]
    assert len(seeded) == 1


def test_seeding_merges_capabilities_and_never_replaces() -> None:
    from app.ai_router.seeder import _register

    reg = ModelRegistry()
    reg.register_configured(
        ModelEndpoint(
            provider="onprem",
            model_id="multi-model",
            display_name="multi-model",
            capabilities=[ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE],
            supports_tools=True,
            extra={"source": "env"},
        )
    )
    _register(reg, "multi-model", [ModelCapability.RERANK], provider="onprem")

    merged = reg.get_configured("onprem", "multi-model")
    assert merged is not None
    assert merged.capabilities == [
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_USE,
        ModelCapability.RERANK,
    ]
    assert merged.supports_tools is True


# ── ColBERT honours COLBERT_CHECKPOINT ───────────────────────────────────────


@pytest.mark.asyncio
async def test_colbert_pattern_uses_the_configured_checkpoint() -> None:
    from app.rag.agentic.patterns.colbert import DEFAULT_COLBERT_CHECKPOINT
    from app.rag.engine import colbert_pattern_from_settings

    pattern = colbert_pattern_from_settings(SimpleNamespace(colbert_checkpoint="my-org/colbert-ft"))
    try:
        assert pattern._reranker.checkpoint == "my-org/colbert-ft"  # type: ignore[attr-defined]
    finally:
        await pattern.aclose()

    default = colbert_pattern_from_settings(SimpleNamespace(colbert_checkpoint=""))
    try:
        assert default._reranker.checkpoint == DEFAULT_COLBERT_CHECKPOINT  # type: ignore[attr-defined]
    finally:
        await default.aclose()
