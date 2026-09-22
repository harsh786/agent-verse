"""KnowledgeGraphFactsSource / SemanticCacheHitsSource — adapters bridging real
backend stores (KnowledgeGraphStore, SemanticCache) to the duck-typed
ContextPipeline `graph_source` / `semantic_cache` contracts.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.context.context_sources import (
    KnowledgeGraphFactsSource,
    SemanticCacheHitsSource,
)


# ── KnowledgeGraphFactsSource ─────────────────────────────────────────────────


def test_get_facts_no_tenant_returns_empty():
    store = MagicMock()
    src = KnowledgeGraphFactsSource(store)
    assert src.get_facts("query") == []
    store.query_nodes.assert_not_called()


def test_get_facts_empty_tenant_string_returns_empty():
    store = MagicMock()
    src = KnowledgeGraphFactsSource(store)
    assert src.get_facts("query", tenant_id="") == []


def test_get_facts_queries_store_with_tenant_and_top_k():
    store = MagicMock()
    store.query_nodes.return_value = []
    src = KnowledgeGraphFactsSource(store)
    src.get_facts("what is X", tenant_id="t1", top_k=3)
    store.query_nodes.assert_called_once_with(tenant_id="t1", search="what is X", limit=3)


def test_get_facts_uses_content_attribute():
    node = SimpleNamespace(content="fact content", node_id="n1", confidence=0.9)
    store = MagicMock()
    store.query_nodes.return_value = [node]
    src = KnowledgeGraphFactsSource(store)
    facts = src.get_facts("q", tenant_id="t1")
    assert facts == [{"fact": "fact content", "node_id": "n1", "confidence": 0.9}]


def test_get_facts_falls_back_to_label_when_no_content():
    node = SimpleNamespace(content="", label="fallback label", node_id="n2", confidence=None)
    store = MagicMock()
    store.query_nodes.return_value = [node]
    src = KnowledgeGraphFactsSource(store)
    facts = src.get_facts("q", tenant_id="t1")
    assert facts == [{"fact": "fallback label", "node_id": "n2", "confidence": None}]


def test_get_facts_skips_nodes_without_content_or_label():
    empty_node = SimpleNamespace(content="", label="")
    good_node = SimpleNamespace(content="kept", node_id="n3", confidence=1.0)
    store = MagicMock()
    store.query_nodes.return_value = [empty_node, good_node]
    src = KnowledgeGraphFactsSource(store)
    facts = src.get_facts("q", tenant_id="t1")
    assert len(facts) == 1
    assert facts[0]["fact"] == "kept"


def test_get_facts_handles_none_result_from_store():
    store = MagicMock()
    store.query_nodes.return_value = None
    src = KnowledgeGraphFactsSource(store)
    assert src.get_facts("q", tenant_id="t1") == []


def test_get_facts_handles_missing_attrs_gracefully():
    """A plain object without node_id/confidence attrs must not raise."""

    class Bare:
        content = "bare content"

    store = MagicMock()
    store.query_nodes.return_value = [Bare()]
    src = KnowledgeGraphFactsSource(store)
    facts = src.get_facts("q", tenant_id="t1")
    assert facts == [{"fact": "bare content", "node_id": None, "confidence": None}]


# ── SemanticCacheHitsSource ───────────────────────────────────────────────────


def test_get_hits_no_tenant_returns_empty():
    cache = MagicMock()
    src = SemanticCacheHitsSource(cache)
    assert src.get_hits("query") == []
    cache.lookup_text.assert_not_called()


def test_get_hits_no_cache_hit_returns_empty():
    cache = MagicMock()
    cache.lookup_text.return_value = None
    src = SemanticCacheHitsSource(cache)
    assert src.get_hits("query", tenant_id="t1") == []
    cache.lookup_text.assert_called_once_with("query", "t1")


def test_get_hits_returns_cached_content():
    cache = MagicMock()
    cache.lookup_text.return_value = "cached answer"
    src = SemanticCacheHitsSource(cache)
    hits = src.get_hits("query", tenant_id="t1", top_k=1)
    assert hits == [{"content": "cached answer"}]
