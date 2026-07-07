# tests/rag/test_agentic/test_layer4_complete.py
"""All 9 Layer 4 RAG agentic files must exist and have standard interface."""
from __future__ import annotations
import pytest
from app.rag.agentic.query_expander import QueryExpander
from app.rag.agentic.retrieval_policy import RetrievalPolicy, RetrievalStrategy
from app.rag.agentic.citation_threader import CitationThreader
from app.rag.agentic.rag_trace import RAGTrace
from app.rag.agentic.query_reformulator import QueryReformulator
from app.rag.agentic.fallback_chain import FallbackChain
from app.rag.agentic.context_gap_detector import ContextGapDetector
from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.agentic.source_inventory import SourceInventory


def test_all_layer4_files_importable():
    files = [
        QueryExpander, RetrievalPolicy, CitationThreader, RAGTrace,
        QueryReformulator, FallbackChain, ContextGapDetector,
        RetrieverTool, SourceInventory,
    ]
    assert len(files) == 9
    assert all(f is not None for f in files)


def test_query_expander_generates_variants():
    expander = QueryExpander()
    variants = expander.expand("list all open Jira tickets")
    assert isinstance(variants, list)
    assert len(variants) >= 1


def test_query_expander_fusion_rag_variants():
    expander = QueryExpander()
    variants = expander.expand_for_fusion("authentication flow", max_variants=3)
    assert len(variants) >= 2
    assert all(isinstance(v, str) for v in variants)
    assert "authentication" in " ".join(variants).lower()


def test_retrieval_policy_selects_hybrid_when_kb_available():
    policy = RetrievalPolicy()
    strategy = policy.select(
        query_type="factual", kb_available=True, web_available=False, kg_available=False
    )
    assert strategy == RetrievalStrategy.HYBRID


def test_retrieval_policy_selects_web_when_kb_empty():
    policy = RetrievalPolicy()
    strategy = policy.select(
        query_type="factual", kb_available=False, web_available=True, kg_available=False
    )
    assert strategy == RetrievalStrategy.WEB


def test_retrieval_policy_selects_graph_for_relationship():
    policy = RetrievalPolicy()
    strategy = policy.select(
        query_type="relationship", kb_available=True, web_available=False, kg_available=True
    )
    assert strategy == RetrievalStrategy.GRAPH


def test_citation_threader_attaches_indices():
    threader = CitationThreader()
    chunks = [
        {"content": "Content A", "source_url": "https://a.com", "chunk_id": "c1"},
        {"content": "Content B", "source_url": "https://b.com", "chunk_id": "c2"},
    ]
    result = threader.thread(chunks)
    assert result[0]["citation_index"] == 1
    assert result[1]["citation_index"] == 2


def test_rag_trace_records_and_emits_sse():
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval("hybrid", "test query", 5, 0.82, 120.0)
    event = trace.to_sse_event()
    assert event["type"] == "rag_strategy_selected"
    assert event["goal_id"] == "g1"
    assert event["steps"] == 1


def test_query_reformulator_generates_2_alternatives():
    reformulator = QueryReformulator(max_attempts=2)
    alts = reformulator.reformulate("what is agentverse")
    assert len(alts) == 2
    assert all(a != "what is agentverse" for a in alts)


def test_fallback_chain_order_matches_doc2():
    chain = FallbackChain()
    assert chain.FALLBACK_ORDER == ["hybrid", "graph", "hyde", "web", "ltm", "parametric"]


def test_context_gap_detector_all_12_signals():
    detector = ContextGapDetector()
    # Each phrase below contains one of the 12 _GAP_SIGNALS substrings:
    # "insufficient", "unclear", "no information", "cannot determine",
    # "lack of context", "not mentioned", "unknown", "not found",
    # "need more", "more context", "cannot verify", "no relevant"
    signals = [
        "Response: insufficient data",
        "Response: unclear result",
        "Response: no information available",
        "Response: cannot determine the answer",
        "Response: lack of context here",
        "Response: not mentioned in the docs",
        "Response: unknown at this time",
        "Response: not found in the system",
        "Response: need more details",
        "Response: more context required",
        "Response: cannot verify this claim",
        "Response: no relevant results",
    ]
    for signal in signals:
        assert detector.has_gap(signal), f"Missing gap signal for: '{signal}'"
