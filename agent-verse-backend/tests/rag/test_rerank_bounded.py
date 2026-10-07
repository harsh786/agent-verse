"""RERANK-BOUNDED: a busy or slow cross-encoder never makes a search miss its deadline.

Live (MongoDB source -> knowledge base -> search) the API pinned 100% CPU and
every knowledge search hit the 30 s retrieval deadline (503). Thread dumps: one
thread in ``model.predict``, the others parked in ``score_sync`` — which opened a
fresh ``ThreadPoolExecutor`` per call, so every concurrent search queued behind
the inference lock without limit; torch ran 8 intra-op threads per call; and no
time budget, candidate cap or token cap bounded a rerank.

These tests use fake slow models (no model download).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.core import cpu
from app.observability.metrics import RERANK_DEGRADED_TOTAL
from app.rag import cross_encoder
from app.rag.cross_encoder import CrossEncoderReranker
from app.rag.engine import RetrievalResult
from app.rag.rerank_budget import (
    DEADLINE_RESERVE_SECONDS,
    rerank_budget_seconds,
    rerank_limits,
    retrieval_deadline,
)
from app.rag.rerank_stage import apply_default_rerank
from app.rag_platform.reranker_contract import BoundedInferenceLane, RerankSkipped

_LANE_PREFIX = "cross-encoder-reranker"


def _count(reason: str) -> float:
    return float(RERANK_DEGRADED_TOTAL.labels(reason=reason)._value.get())


class _SlowModel:
    """Fake cross-encoder: ``delay`` seconds per call (optionally gated), records calls.

    Scores a passage by how many query words it contains (ms-marco logit scale).
    """

    def __init__(self, delay: float = 0.0, *, gated: bool = False) -> None:
        self.delay = delay
        self.gate = threading.Event()
        if not gated:
            self.gate.set()
        self.started = threading.Event()
        self.calls: list[int] = []
        self.threads: set[str] = set()
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def predict(self, pairs: list[tuple[str, str]], *, batch_size: int) -> list[float]:
        del batch_size
        with self._lock:
            self.calls.append(len(pairs))
            self.threads.add(threading.current_thread().name)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            self.gate.wait(timeout=10)
            if self.delay:
                time.sleep(self.delay)
            return [
                float(10 * sum(w in doc for w in query.split()) - 10) for query, doc in pairs
            ]
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def make_reranker() -> Any:
    created: list[CrossEncoderReranker] = []

    def _make(model: _SlowModel, *, queue_depth: int = 0) -> CrossEncoderReranker:
        reranker = CrossEncoderReranker(model_loader=lambda: model, max_queue_size=queue_depth)
        reranker.start_warmup().result(timeout=5)
        created.append(reranker)
        return reranker

    yield _make
    for reranker in created:
        reranker.close_sync()


@pytest.fixture
def default_reranker(monkeypatch: pytest.MonkeyPatch, make_reranker: Any) -> Any:
    """Install a warm fake as the process-default cross-encoder."""

    def _install(model: _SlowModel, *, queue_depth: int = 0) -> CrossEncoderReranker:
        reranker = make_reranker(model, queue_depth=queue_depth)
        monkeypatch.setattr(cross_encoder, "_default_reranker", reranker)
        return reranker

    return _install


def _settings(**extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        rag_default_rerank_enabled=True,
        rag_default_rerank_strategy=extra.pop("strategy", "cross_encoder"),
        rag_rerank_warmup_wait_seconds=0.05,
        rag_rerank_budget_ms=extra.pop("budget_ms", 2500),
        rag_rerank_max_candidates=extra.pop("max_candidates", 30),
        **extra,
    )


def _results(count: int = 3) -> list[RetrievalResult]:
    # Retrieval order c0, c1, ...; the LAST one is what the cross-encoder prefers.
    return [
        RetrievalResult(
            chunk_id=f"c{i}",
            content="alpha beta" if i == count - 1 else f"unrelated passage {i}",
            score=1.0 - i * 0.01,
            source_metadata={},
        )
        for i in range(count)
    ]


def _chunks(count: int) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": f"c{i}",
            "content": "alpha beta" if i == count - 1 else f"unrelated passage {i}",
            "score": 1.0 - i * 0.01,
        }
        for i in range(count)
    ]


def _occupy_lane(reranker: CrossEncoderReranker, model: _SlowModel) -> threading.Thread:
    """Start a sync score that holds the lane's worker until ``model.gate`` is set."""
    worker = threading.Thread(
        target=reranker.score_sync, args=("alpha", ["held"]), daemon=True
    )
    worker.start()
    assert model.started.wait(timeout=5)
    return worker


# ── The bounded lane itself ──────────────────────────────────────────────────


async def test_a_full_queue_is_refused_at_once_not_queued(make_reranker: Any) -> None:
    model = _SlowModel(gated=True)
    reranker = make_reranker(model, queue_depth=0)
    worker = _occupy_lane(reranker, model)
    try:
        started = time.monotonic()
        with pytest.raises(RerankSkipped) as sync_skip:
            reranker.score_sync("alpha", ["x"], budget_seconds=5.0)
        with pytest.raises(RerankSkipped) as async_skip:
            await reranker.score("alpha", ["x"], budget_seconds=5.0)
        assert time.monotonic() - started < 0.5  # refused, never waited
        assert sync_skip.value.reason == "busy"
        assert async_skip.value.reason == "busy"
    finally:
        model.gate.set()
        worker.join(timeout=5)
    assert model.calls == [1]  # the refused requests never reached the model


async def test_score_sync_and_score_share_one_bounded_lane(make_reranker: Any) -> None:
    """``score_sync`` used to open a ThreadPoolExecutor per call (no bound at all)."""
    model = _SlowModel()
    reranker = make_reranker(model, queue_depth=0)
    sync_scores = reranker.score_sync("alpha", ["alpha", "zzz"])
    async_scores = await reranker.score("alpha", ["alpha", "zzz"])
    assert sync_scores == async_scores == [0.0, -10.0]
    assert len(model.threads) == 1  # the same worker served both
    (thread_name,) = model.threads
    assert thread_name.startswith(_LANE_PREFIX)
    assert thread_name != threading.main_thread().name


def test_concurrent_sync_callers_never_exceed_the_lane(make_reranker: Any) -> None:
    """12 sync searches while the model is busy: 2 queue, 10 are refused at once."""
    model = _SlowModel(gated=True)
    reranker = make_reranker(model, queue_depth=2)
    lanes_before = {t.name for t in threading.enumerate() if t.name.startswith(_LANE_PREFIX)}
    worker = _occupy_lane(reranker, model)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _search() -> None:
        try:
            reranker.score_sync("alpha", ["alpha"], budget_seconds=5.0)
            outcome = "scored"
        except RerankSkipped as skip:
            outcome = skip.reason
        with lock:
            outcomes.append(outcome)

    callers = [threading.Thread(target=_search) for _ in range(12)]
    try:
        for caller in callers:
            caller.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with lock:
                if len(outcomes) >= 10:
                    break
            time.sleep(0.01)
        with lock:
            refused = list(outcomes)
        assert refused == ["busy"] * 10  # refused while the 2 queue slots are taken
        assert reranker._get_lane().pending == 3  # 1 running + 2 queued, never more
    finally:
        model.gate.set()
        for caller in callers:
            caller.join(timeout=10)
        worker.join(timeout=5)

    assert sorted(outcomes) == ["busy"] * 10 + ["scored"] * 2
    assert model.max_active == 1  # inference stays serialised
    assert model.calls == [1, 1, 1]
    lane_threads = {t.name for t in threading.enumerate() if t.name.startswith(_LANE_PREFIX)}
    # No per-call executors: the one lane worker served every call.
    assert len(model.threads) == 1
    assert len(lane_threads - lanes_before) <= 1


async def test_a_job_that_would_not_finish_in_budget_is_refused_without_waiting(
    make_reranker: Any,
) -> None:
    model = _SlowModel(delay=0.3)
    reranker = make_reranker(model, queue_depth=4)
    await reranker.score("alpha", ["a", "b"])  # teaches the lane ~0.15 s per pair
    model.delay = 0.0
    model.gate.clear()
    model.started.clear()
    worker = _occupy_lane(reranker, model)
    try:
        started = time.monotonic()
        with pytest.raises(RerankSkipped) as skip:
            await reranker.score("alpha", ["x"] * 4, budget_seconds=0.4)
        assert skip.value.reason == "budget_exceeded"
        assert time.monotonic() - started < 0.2  # predicted, not waited out
    finally:
        model.gate.set()
        worker.join(timeout=5)


async def test_an_idle_lane_always_admits_even_after_slow_calls(make_reranker: Any) -> None:
    """A pessimistic estimate must never lock the model out for good."""
    model = _SlowModel(delay=0.3)
    reranker = make_reranker(model, queue_depth=0)
    await reranker.score("alpha", ["a"])  # 0.3 s per pair observed
    model.delay = 0.0
    assert await reranker.score("alpha", ["alpha"], budget_seconds=0.1) == [0.0]


async def test_a_wait_past_the_budget_gives_up_and_cancels_the_queued_job(
    make_reranker: Any,
) -> None:
    model = _SlowModel(gated=True)
    reranker = make_reranker(model, queue_depth=1)
    worker = _occupy_lane(reranker, model)
    try:
        started = time.monotonic()
        with pytest.raises(RerankSkipped) as skip:
            await reranker.score("alpha", ["queued"], budget_seconds=0.2)
        elapsed = time.monotonic() - started
        assert skip.value.reason == "budget_exceeded"
        assert 0.15 <= elapsed < 0.6
        lane = reranker._get_lane()
        assert lane.pending == 1  # the queued job was cancelled, only the held one is left
    finally:
        model.gate.set()
        worker.join(timeout=5)
    assert model.calls == [1]  # the cancelled job never ran


def test_lane_validates_its_bounds() -> None:
    with pytest.raises(ValueError):
        BoundedInferenceLane(max_workers=0, max_queue_depth=1, thread_name_prefix="x")
    with pytest.raises(ValueError):
        BoundedInferenceLane(max_workers=1, max_queue_depth=-1, thread_name_prefix="x")
    with pytest.raises(ValueError):
        CrossEncoderReranker(max_queue_size=-1)


async def test_a_cold_model_is_skipped_within_the_budget_never_loaded_inline() -> None:
    release = threading.Event()
    loader_threads: list[str] = []

    def _slow_load() -> _SlowModel:
        loader_threads.append(threading.current_thread().name)
        release.wait(timeout=10)
        return _SlowModel()

    reranker = CrossEncoderReranker(model_loader=_slow_load, max_queue_size=0)
    try:
        started = time.monotonic()
        with pytest.raises(RerankSkipped) as skip:
            await reranker.score("alpha", ["x"], budget_seconds=0.1)
        assert skip.value.reason == "warming_up"
        assert time.monotonic() - started < 0.5
        with pytest.raises(RerankSkipped):
            reranker.score_sync("alpha", ["x"], budget_seconds=0.1)
        assert loader_threads == ["cross-encoder-warmup"]  # never the caller's thread
    finally:
        release.set()
        reranker.close_sync()


# ── The policy (sync and async) and the default-path stage ───────────────────


def test_sync_policy_keeps_retrieval_order_when_busy(
    default_reranker: Any,
) -> None:
    model = _SlowModel(gated=True)
    reranker = default_reranker(model, queue_depth=0)
    worker = _occupy_lane(reranker, model)
    before = _count("busy")
    try:
        policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER, max_per_source=0)
        out = policy.rerank(_chunks(3), query="alpha beta")
    finally:
        model.gate.set()
        worker.join(timeout=5)
    assert [c["chunk_id"] for c in out] == ["c0", "c1", "c2"]  # retrieval order
    assert policy.last_skipped_reason == "busy"
    assert policy.last_degraded_reason is None  # skipped, not a TF-IDF fallback
    assert _count("busy") == before + 1


async def test_budget_exceeded_returns_retrieval_order_flagged(default_reranker: Any) -> None:
    default_reranker(_SlowModel(delay=1.0), queue_depth=4)
    before = _count("budget_exceeded")
    started = time.monotonic()
    out = await apply_default_rerank(
        _results(3), query="alpha beta", query_embedding=None, settings=_settings(budget_ms=200)
    )
    elapsed = time.monotonic() - started
    assert elapsed < 0.2 + 0.3  # the budget, not the 1 s model call
    assert [r.chunk_id for r in out] == ["c0", "c1", "c2"]
    assert all(r.source_metadata["rerank_skipped"] == "budget_exceeded" for r in out)
    assert all("pre_rerank_score" not in r.source_metadata for r in out)
    assert _count("budget_exceeded") == before + 1


async def test_auto_when_busy_keeps_its_documented_score_order(default_reranker: Any) -> None:
    model = _SlowModel(gated=True)
    reranker = default_reranker(model, queue_depth=0)
    worker = _occupy_lane(reranker, model)
    results = _results(3)
    results[0].score, results[1].score = results[1].score, results[0].score  # c1 > c0
    try:
        started = time.monotonic()
        out = await apply_default_rerank(
            results, query="alpha beta", query_embedding=None, settings=_settings(strategy="auto")
        )
        assert time.monotonic() - started < 0.5  # refused at once
    finally:
        model.gate.set()
        worker.join(timeout=5)
    assert [r.chunk_id for r in out] == ["c1", "c0", "c2"]
    assert all(r.source_metadata["rerank_skipped"] == "busy" for r in out)
    assert all(r.source_metadata["rerank_strategy"] == "score" for r in out)


async def test_only_the_top_candidates_are_cross_encoded(default_reranker: Any) -> None:
    model = _SlowModel()
    default_reranker(model, queue_depth=4)
    results = _results(10)
    # Put a strong match INSIDE the window so the head is visibly reordered.
    results[2].content = "alpha beta"
    out = await apply_default_rerank(
        results, query="alpha beta", query_embedding=None, settings=_settings(max_candidates=4)
    )
    assert model.calls == [4]  # the model saw the top 4 only
    ids = [r.chunk_id for r in out]
    assert ids[0] == "c2"  # reranked within the window
    assert set(ids[:4]) == {"c0", "c1", "c2", "c3"}  # the head stays the head
    assert ids[4:] == [f"c{i}" for i in range(4, 10)]  # the rest, in retrieval order
    assert all(r.source_metadata.get("rerank_beyond_window") for r in out[4:])
    assert not any(r.source_metadata.get("rerank_beyond_window") for r in out[:4])
    assert all(r.source_metadata.get("rerank_strategy") == "cross_encoder" for r in out)


def test_sync_policy_caps_candidates_too(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def _fake(query: str, docs: list[str], batch_size: int = 32) -> list[float]:
        seen.append(len(docs))
        return [float(-i) for i in range(len(docs))]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake)
    policy = RerankPolicy(
        strategy=RerankStrategy.CROSS_ENCODER, max_per_source=0, max_rerank_candidates=3
    )
    out = policy.rerank(_chunks(7), query="q")
    assert seen == [3]
    assert [c["chunk_id"] for c in out[3:]] == ["c3", "c4", "c5", "c6"]


async def test_the_retrieval_deadline_caps_the_rerank(default_reranker: Any) -> None:
    model = _SlowModel()
    default_reranker(model, queue_depth=4)
    with retrieval_deadline(DEADLINE_RESERVE_SECONDS + 0.05):
        assert rerank_budget_seconds(_settings()) <= 0.05
    with retrieval_deadline(DEADLINE_RESERVE_SECONDS / 2):
        assert rerank_budget_seconds(_settings()) == 0.0
        out = await apply_default_rerank(
            _results(3), query="alpha beta", query_embedding=None, settings=_settings()
        )
    assert [r.chunk_id for r in out] == ["c0", "c1", "c2"]
    assert all(r.source_metadata["rerank_skipped"] == "budget_exceeded" for r in out)
    assert model.calls == []  # no time left: the model is not even asked


async def test_the_gateway_publishes_its_strategy_deadline() -> None:
    import inspect

    from app.rag import gateway

    source = inspect.getsource(gateway)
    assert "with retrieval_deadline(self.dependencies.strategy_timeout_seconds):" in source


def test_rerank_limits_defaults_and_settings() -> None:
    limits = rerank_limits(SimpleNamespace())
    assert limits.budget_seconds == 2.5
    assert limits.max_queue_depth == 4
    assert limits.max_candidates == 30
    assert limits.max_length == 256
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["rag_rerank_budget_ms"].default == 2500
    assert fields["rag_rerank_max_queue_depth"].default == 4
    assert fields["rag_rerank_max_candidates"].default == 30
    assert fields["rag_rerank_max_length"].default == 256
    assert fields["rag_rerank_torch_threads"].default == 0


# ── The live regression: N concurrent searches against a 1 s model ───────────


async def test_concurrent_searches_finish_within_budget_and_extras_are_flagged(
    default_reranker: Any,
) -> None:
    """8 searches at once, a 1 s model, a 2.5 s budget, 4 queue slots.

    Before: each search parked a thread behind the inference lock, so the 8th
    waited ~8 s (live: past the 30 s deadline, 503). Now every search returns
    within the budget; those the model could not serve in time are flagged.
    """
    model = _SlowModel(delay=1.0)
    reranker = default_reranker(model, queue_depth=4)
    budget = 2.5
    settings = _settings(budget_ms=int(budget * 1000))
    before = {reason: _count(reason) for reason in ("busy", "budget_exceeded")}

    async def _search() -> tuple[float, list[RetrievalResult]]:
        started = time.monotonic()
        out = await apply_default_rerank(
            _results(3), query="alpha beta", query_embedding=None, settings=settings
        )
        return time.monotonic() - started, out

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(_ticker())
    try:
        outcomes = await asyncio.gather(*(_search() for _ in range(8)))
    finally:
        ticker.cancel()

    epsilon = 0.4
    assert max(elapsed for elapsed, _ in outcomes) < budget + epsilon
    reranked = [out for _, out in outcomes if "rerank_skipped" not in out[0].source_metadata]
    skipped = [out for _, out in outcomes if "rerank_skipped" in out[0].source_metadata]
    assert 1 <= len(reranked) <= 2  # what a 1 s model can serve in 2.5 s
    assert len(skipped) == 8 - len(reranked)
    for out in reranked:
        assert out[0].chunk_id == "c2"  # the cross-encoder's pick
        assert out[0].source_metadata["rerank_strategy"] == "cross_encoder"
    for out in skipped:
        assert [r.chunk_id for r in out] == ["c0", "c1", "c2"]  # retrieval order
        reasons = {r.source_metadata["rerank_skipped"] for r in out}
        assert len(reasons) == 1 and reasons <= {"busy", "budget_exceeded"}
    # 8 searches > 1 running + 4 queued: some were refused outright.
    flagged = {
        reason: _count(reason) - before[reason] for reason in ("busy", "budget_exceeded")
    }
    assert flagged["busy"] >= 3
    assert flagged["busy"] + flagged["budget_exceeded"] == len(skipped)
    assert model.max_active == 1
    assert ticks >= 100  # the event loop kept running throughout
    assert reranker._get_lane().pending <= 1  # nothing left queued behind the budget


# ── torch threads and the model's token limit ────────────────────────────────


class _FakeTorch:
    def __init__(self, *, interop_error: bool = False) -> None:
        self.num_threads: list[int] = []
        self.interop_threads: list[int] = []
        self.interop_error = interop_error

    def set_num_threads(self, count: int) -> None:
        self.num_threads.append(count)

    def set_num_interop_threads(self, count: int) -> None:
        if self.interop_error:
            raise RuntimeError("Error: cannot set number of interop threads after parallel work")
        self.interop_threads.append(count)


@pytest.fixture
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(**kwargs: Any) -> _FakeTorch:
        torch = _FakeTorch(**kwargs)
        monkeypatch.setitem(sys.modules, "torch", torch)
        monkeypatch.setattr(cross_encoder, "_torch_threads_configured", None)
        return torch

    return _install


@pytest.mark.parametrize(("cpus", "expected"), [(8, 2), (1, 1)])
def test_auto_torch_threads_respect_available_cpus(
    fake_torch: Any, monkeypatch: pytest.MonkeyPatch, cpus: int, expected: int
) -> None:
    torch = fake_torch()
    monkeypatch.setattr(cpu, "available_cpus", lambda quota=None: cpus)
    assert cross_encoder.configure_torch_threads(0, 1) == (expected, 1)
    assert torch.num_threads == [expected]
    assert torch.interop_threads == [1]
    # Once per process: a second model load does not touch torch again.
    assert cross_encoder.configure_torch_threads(5, 1) == (expected, 1)
    assert torch.num_threads == [expected]


def test_explicit_torch_threads_and_a_late_interop_setting(fake_torch: Any) -> None:
    torch = fake_torch(interop_error=True)
    assert cross_encoder.configure_torch_threads(3, 1) == (3, None)
    assert torch.num_threads == [3]  # intra-op still applied


def test_model_load_pins_threads_and_the_token_limit(
    fake_torch: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    torch = fake_torch()
    created: list[dict[str, Any]] = []

    class _FakeCrossEncoder:
        def __init__(self, name: str, **kwargs: Any) -> None:
            created.append({"name": name, **kwargs})

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=_FakeCrossEncoder)
    )
    monkeypatch.setattr(
        cross_encoder,
        "rerank_limits",
        lambda settings=None: rerank_limits(
            SimpleNamespace(
                rag_rerank_max_length=128,
                rag_rerank_torch_threads=2,
                rag_rerank_torch_interop_threads=1,
            )
        ),
    )
    cross_encoder._load_cross_encoder()
    assert torch.num_threads == [2]  # before the model is built
    assert created == [{"name": cross_encoder._CROSS_ENCODER_MODEL, "max_length": 128}]


def test_available_cpus_honour_the_cgroup_quota(tmp_path: Any) -> None:
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("150000 100000\n")
    quota = cpu.cgroup_cpu_quota(
        v2_cpu_max=cpu_max, v1_quota=tmp_path / "x", v1_period=tmp_path / "y"
    )
    assert quota == 1.5
    assert cpu.available_cpus(quota) <= 2
    cpu_max.write_text("max 100000\n")
    assert (
        cpu.cgroup_cpu_quota(v2_cpu_max=cpu_max, v1_quota=tmp_path / "x", v1_period=tmp_path / "y")
        is None
    )


def test_availability_check_never_loads_or_probes_inline() -> None:
    release = threading.Event()
    threads: list[str] = []

    def _slow_load() -> _SlowModel:
        threads.append(threading.current_thread().name)
        release.wait(timeout=10)
        return _SlowModel()

    reranker = CrossEncoderReranker(model_loader=_slow_load)
    original = cross_encoder._default_reranker
    cross_encoder._default_reranker = reranker
    try:
        started = time.monotonic()
        assert cross_encoder.is_cross_encoder_available() is False
        assert time.monotonic() - started < 0.5
        release.set()
        assert cross_encoder.is_cross_encoder_available(wait_seconds=5) is True
        assert threads == ["cross-encoder-warmup"]
    finally:
        cross_encoder._default_reranker = original
        release.set()
        reranker.close_sync()
