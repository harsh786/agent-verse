# tests/context/test_context_producers.py
"""BK3 (D-20 follow-up): ContextPipeline-level producers for graph_facts and
semantic_cache_hits.

Prior state (see tests/context/test_context_pipeline.py's D-20 tests):
ContextPipeline.run() already *threads* graph_facts / semantic_cache_hits
through to PromptBuilder when a caller passes them explicitly — but nothing
ever populated them, so the two builder branches stayed dead in practice.

This adds two optional, best-effort producers, injected via ContextPipeline's
constructor (mirroring the two-phase optional-dependency pattern used
elsewhere in the pipeline — no hard-imported globals):

  - ``graph_source``: any object exposing
    ``get_facts(query, tenant_id=None, top_k=5) -> list[dict]``
  - ``semantic_cache``: any object exposing
    ``get_hits(query, tenant_id=None, top_k=2) -> list[dict]``

When the caller omits ``graph_facts`` / ``semantic_cache_hits`` from
``run()`` (i.e. leaves them ``None``), the pipeline asks the injected source
for them, best-effort (any exception, or an absent source, degrades to an
empty list — never raises). An explicit argument (including ``[]``) always
takes precedence over the producer.
"""
from __future__ import annotations

import pytest

from app.context.context_pipeline import ContextPipeline


@pytest.fixture
def sample_chunks():
    return [
        {
            "chunk_id": "c1",
            "content": "AgentVerse supports dynamic orchestration.",
            "score": 0.9,
            "source_url": "https://docs.example.com/page1",
        },
    ]


class _FakeGraphSource:
    """Fake knowledge-graph source used to prove the producer wiring."""

    def __init__(self, facts=None, raise_error=False):
        self._facts = facts if facts is not None else []
        self._raise = raise_error

    def get_facts(self, query, tenant_id=None, top_k=5):
        if self._raise:
            raise RuntimeError("kg store unavailable")
        return self._facts


class _FakeSemanticCache:
    """Fake semantic cache used to prove the producer wiring."""

    def __init__(self, hits=None, raise_error=False):
        self._hits = hits if hits is not None else []
        self._raise = raise_error

    def get_hits(self, query, tenant_id=None, top_k=2):
        if self._raise:
            raise RuntimeError("cache unavailable")
        return self._hits


# ── graph_facts producer ───────────────────────────────────────────────────


def test_graph_source_populates_graph_facts_when_caller_omits_them(sample_chunks):
    """A ContextPipeline given a fake KG source returning facts populates
    graph_facts (via the producer) when run() isn't given graph_facts
    explicitly — so PromptBuilder renders the 'Knowledge graph context'
    section end-to-end."""
    fake_source = _FakeGraphSource(
        facts=[
            {"fact": "NodeAlpha relates to NodeBeta"},
            {"fact": "NodeGamma is a dependency of NodeAlpha"},
        ]
    )
    pipeline = ContextPipeline(max_tokens=6000, graph_source=fake_source)
    result = pipeline.run(
        chunks=sample_chunks,
        query="orchestration",
        goal_context="explain orchestration",
        tenant_id="tenant-1",
    )
    assert "Knowledge graph context" in result.planner_context
    assert "NodeAlpha relates to NodeBeta" in result.planner_context


def test_graph_source_empty_or_erroring_degrades_to_empty(sample_chunks):
    """No KG data, or the store raising, both degrade cleanly to an empty
    branch — no exception propagates."""
    empty_source = _FakeGraphSource(facts=[])
    pipeline = ContextPipeline(max_tokens=6000, graph_source=empty_source)
    result = pipeline.run(
        chunks=sample_chunks, query="orchestration", goal_context="g", tenant_id="tenant-1"
    )
    assert "Knowledge graph context" not in result.planner_context

    erroring_source = _FakeGraphSource(raise_error=True)
    pipeline2 = ContextPipeline(max_tokens=6000, graph_source=erroring_source)
    result2 = pipeline2.run(
        chunks=sample_chunks, query="orchestration", goal_context="g", tenant_id="tenant-1"
    )
    assert "Knowledge graph context" not in result2.planner_context


# ── semantic_cache_hits producer ───────────────────────────────────────────


def test_semantic_cache_source_populates_hits_on_hit(sample_chunks):
    """A fake semantic cache returning a hit populates semantic_cache_hits so
    PromptBuilder renders the 'Cached context' section."""
    fake_cache = _FakeSemanticCache(hits=[{"content": "cached answer for orchestration"}])
    pipeline = ContextPipeline(max_tokens=6000, semantic_cache=fake_cache)
    result = pipeline.run(
        chunks=sample_chunks, query="orchestration", goal_context="g", tenant_id="tenant-1"
    )
    assert "Cached context" in result.planner_context
    assert "cached answer for orchestration" in result.planner_context


def test_semantic_cache_source_miss_or_erroring_degrades_to_empty(sample_chunks):
    """Miss, or the cache raising, both degrade cleanly to an empty branch."""
    missed_cache = _FakeSemanticCache(hits=[])
    pipeline = ContextPipeline(max_tokens=6000, semantic_cache=missed_cache)
    result = pipeline.run(
        chunks=sample_chunks, query="orchestration", goal_context="g", tenant_id="tenant-1"
    )
    assert "Cached context" not in result.planner_context

    erroring_cache = _FakeSemanticCache(raise_error=True)
    pipeline2 = ContextPipeline(max_tokens=6000, semantic_cache=erroring_cache)
    result2 = pipeline2.run(
        chunks=sample_chunks, query="orchestration", goal_context="g", tenant_id="tenant-1"
    )
    assert "Cached context" not in result2.planner_context


# ── clean degradation without the new deps ─────────────────────────────────


def test_pipeline_without_new_deps_behaves_exactly_as_before(sample_chunks):
    """A pipeline constructed WITHOUT graph_source/semantic_cache must behave
    identically to before this change: no crash, both branches stay empty,
    regardless of whether query/tenant_id are supplied to run()."""
    pipeline = ContextPipeline(max_tokens=6000)

    result = pipeline.run(chunks=sample_chunks, query="orchestration", goal_context="g")
    assert "Knowledge graph context" not in result.planner_context
    assert "Cached context" not in result.planner_context
    assert result.included_chunks  # existing chunk behavior untouched

    # Even with a tenant_id, no source means nothing to query -> still empty.
    result2 = pipeline.run(
        chunks=sample_chunks, query="orchestration", goal_context="g", tenant_id="tenant-1"
    )
    assert "Knowledge graph context" not in result2.planner_context
    assert "Cached context" not in result2.planner_context


def test_explicit_arguments_take_precedence_over_injected_sources(sample_chunks):
    """A caller-supplied graph_facts=[] / semantic_cache_hits=[] bypasses the
    injected producer entirely — preserving the original explicit-argument
    contract from the D-20 threading work."""
    fake_source = _FakeGraphSource(facts=[{"fact": "ShouldNotAppear"}])
    fake_cache = _FakeSemanticCache(hits=[{"content": "ShouldNotAppearEither"}])
    pipeline = ContextPipeline(max_tokens=6000, graph_source=fake_source, semantic_cache=fake_cache)
    result = pipeline.run(
        chunks=sample_chunks,
        query="orchestration",
        goal_context="g",
        tenant_id="tenant-1",
        graph_facts=[],
        semantic_cache_hits=[],
    )
    assert "Knowledge graph context" not in result.planner_context
    assert "ShouldNotAppear" not in result.planner_context
    assert "Cached context" not in result.planner_context
    assert "ShouldNotAppearEither" not in result.planner_context
