"""RERANK-PRELOAD: the cross-encoder is warmed in the background at startup, and
a search never burns its retrieval deadline on the model load.

The first search after a restart used to load the cross-encoder synchronously
inside the default rerank stage (``auto`` probed availability by loading the
model on the event loop), so it hit the 30 s strategy deadline and answered 503.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.observability.metrics import RERANK_DEGRADED_TOTAL
from app.rag import cross_encoder
from app.rag.cross_encoder import CrossEncoderReranker
from app.rag.engine import RetrievalResult
from app.rag.rerank_stage import apply_default_rerank
from app.rag_platform.reranker_contract import RerankerLoadError


class _Backend:
    """Scores a passage by how many query words it contains."""

    def predict(self, pairs: list[tuple[str, str]], *, batch_size: int) -> list[float]:
        del batch_size
        return [float(sum(w in doc for w in query.split())) for query, doc in pairs]


class _GatedLoader:
    """A model loader that blocks until released (a slow first model load)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.release = threading.Event()
        self.calls = 0
        self.fail = fail

    def __call__(self) -> _Backend:
        self.calls += 1
        self.release.wait(timeout=10)
        if self.fail:
            raise RerankerLoadError("no model")
        return _Backend()


@pytest.fixture
def swap_default(monkeypatch: pytest.MonkeyPatch) -> Any:
    created: list[CrossEncoderReranker] = []

    def _install(loader: Any) -> CrossEncoderReranker:
        reranker = CrossEncoderReranker(model_loader=loader)
        created.append(reranker)
        monkeypatch.setattr(cross_encoder, "_default_reranker", reranker)
        return reranker

    yield _install
    for reranker in created:
        reranker.close_sync()


def _settings(strategy: str, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        rag_default_rerank_enabled=True,
        rag_default_rerank_strategy=strategy,
        rag_rerank_warmup_wait_seconds=extra.pop("wait", 0.05),
        rag_rerank_preload=extra.pop("preload", True),
        **extra,
    )


def _results() -> list[RetrievalResult]:
    return [
        RetrievalResult(chunk_id="c1", content="nothing relevant", score=0.9, source_metadata={}),
        RetrievalResult(chunk_id="c2", content="alpha beta", score=0.5, source_metadata={}),
    ]


# ── Background warm-up on the reranker itself ────────────────────────────────


async def test_start_warmup_loads_in_background_once() -> None:
    loader = _GatedLoader()
    reranker = CrossEncoderReranker(model_loader=loader)
    try:
        started = time.monotonic()
        future = reranker.start_warmup()
        assert time.monotonic() - started < 0.5  # never blocks the caller
        assert reranker.is_ready is False
        assert reranker.start_warmup() is future  # idempotent while loading
        loader.release.set()
        await asyncio.wait_for(asyncio.wrap_future(future), 5)
        assert reranker.is_ready is True
        assert loader.calls == 1
        assert await reranker.score("alpha", ["alpha", "zzz"]) == [1.0, 0.0]
        assert loader.calls == 1  # the warm model is reused
    finally:
        await reranker.aclose()


async def test_failed_warmup_is_reported_and_retried_after_backoff() -> None:
    loader = _GatedLoader(fail=True)
    loader.release.set()
    reranker = CrossEncoderReranker(model_loader=loader, warmup_retry_seconds=0.0)
    try:
        first = reranker.start_warmup()
        with pytest.raises(RerankerLoadError):
            await asyncio.wait_for(asyncio.wrap_future(first), 5)
        assert reranker.is_ready is False
        second = reranker.start_warmup()
        assert second is not first  # a failed load is retried (after the backoff)
        with pytest.raises(RerankerLoadError):
            await asyncio.wait_for(asyncio.wrap_future(second), 5)
        assert loader.calls == 2
    finally:
        await reranker.aclose()


# ── The search path never waits past its warm-up budget ──────────────────────


async def test_search_skips_rerank_honestly_while_the_model_warms(swap_default: Any) -> None:
    loader = _GatedLoader()
    swap_default(loader)
    before = RERANK_DEGRADED_TOTAL.labels(reason="reranker_warming_up")._value.get()
    try:
        started = time.monotonic()
        out = await apply_default_rerank(
            _results(), query="alpha", query_embedding=None, settings=_settings("cross_encoder")
        )
        elapsed = time.monotonic() - started
    finally:
        loader.release.set()
    assert elapsed < 1.0  # bounded by the warm-up budget, not the model load
    assert [r.chunk_id for r in out] == ["c1", "c2"]  # original order, nothing dropped
    assert all(r.source_metadata.get("rerank_skipped") == "reranker_warming_up" for r in out)
    after = RERANK_DEGRADED_TOTAL.labels(reason="reranker_warming_up")._value.get()
    assert after == before + 1
    assert loader.calls == 1  # the search kicked off the warm-up itself


async def test_auto_does_not_load_the_model_on_the_event_loop(swap_default: Any) -> None:
    loader = _GatedLoader()
    swap_default(loader)
    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(_ticker())
    try:
        out = await apply_default_rerank(
            _results(),
            query="alpha",
            query_embedding=None,
            settings=_settings("auto", wait=0.1),
        )
    finally:
        loader.release.set()
        ticker.cancel()
    assert ticks >= 5  # the loop kept running while the model was loading
    assert all(r.source_metadata.get("rerank_skipped") == "reranker_warming_up" for r in out)
    # auto degrades to the deterministic score order until the model is warm
    assert [r.chunk_id for r in out] == ["c1", "c2"]
    assert out[0].source_metadata.get("rerank_strategy") == "score"


async def test_auto_uses_the_cross_encoder_once_warm(swap_default: Any) -> None:
    loader = _GatedLoader()
    loader.release.set()
    reranker = swap_default(loader)
    await asyncio.wait_for(asyncio.wrap_future(reranker.start_warmup()), 5)
    out = await apply_default_rerank(
        _results(), query="alpha beta", query_embedding=None, settings=_settings("auto")
    )
    assert [r.chunk_id for r in out] == ["c2", "c1"]
    assert out[0].source_metadata.get("rerank_strategy") == "cross_encoder"
    assert "rerank_skipped" not in out[0].source_metadata


async def test_search_waits_for_warmup_within_budget(swap_default: Any) -> None:
    loader = _GatedLoader()
    swap_default(loader)
    threading.Timer(0.05, loader.release.set).start()
    out = await apply_default_rerank(
        _results(),
        query="alpha beta",
        query_embedding=None,
        settings=_settings("cross_encoder", wait=5.0),
    )
    assert [r.chunk_id for r in out] == ["c2", "c1"]
    assert "rerank_skipped" not in out[0].source_metadata


async def test_unloadable_model_under_auto_falls_back_to_score(swap_default: Any) -> None:
    loader = _GatedLoader(fail=True)
    loader.release.set()
    swap_default(loader)
    out = await apply_default_rerank(
        _results(), query="alpha", query_embedding=None, settings=_settings("auto", wait=2.0)
    )
    assert [r.chunk_id for r in out] == ["c1", "c2"]
    assert out[0].source_metadata.get("rerank_strategy") == "score"
    assert out[0].source_metadata.get("rerank_skipped") == "reranker_unavailable"


# ── Preload at app startup and in Celery workers ─────────────────────────────


def test_preload_starts_background_warmup(swap_default: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    loader = _GatedLoader()
    reranker = swap_default(loader)
    monkeypatch.setattr(cross_encoder, "_sentence_transformers_installed", lambda: True)
    try:
        future = cross_encoder.preload_default_cross_encoder(_settings("auto"))
        assert future is not None
        assert future is reranker.start_warmup()
    finally:
        loader.release.set()


@pytest.mark.parametrize(
    "settings",
    [
        _settings("auto", preload=False),
        _settings("score"),
        _settings("hosted"),
        SimpleNamespace(rag_default_rerank_enabled=False, rag_default_rerank_strategy="auto"),
    ],
)
def test_preload_is_skipped_when_the_cross_encoder_is_not_used(
    settings: SimpleNamespace, swap_default: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = _GatedLoader()
    swap_default(loader)
    monkeypatch.setattr(cross_encoder, "_sentence_transformers_installed", lambda: True)
    assert cross_encoder.preload_default_cross_encoder(settings) is None
    assert loader.calls == 0


def test_preload_is_skipped_without_sentence_transformers(
    swap_default: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = _GatedLoader()
    swap_default(loader)
    monkeypatch.setattr(cross_encoder, "_sentence_transformers_installed", lambda: False)
    assert cross_encoder.preload_default_cross_encoder(_settings("auto")) is None
    assert loader.calls == 0


def test_celery_worker_process_init_preloads_the_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import celery_app

    calls: list[Any] = []
    monkeypatch.setattr(
        cross_encoder, "preload_default_cross_encoder", lambda settings=None: calls.append(1)
    )
    celery_app._preload_retrieval_models()
    assert calls == [1]


def test_app_lifespan_preloads_the_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    calls: list[Any] = []
    monkeypatch.setattr(
        cross_encoder, "preload_default_cross_encoder", lambda settings=None: calls.append(1)
    )
    app = create_app()
    with TestClient(app):
        pass
    assert calls == [1]
