"""_node_rag_prime, _node_rag_remediate, _node_refine — doc-2 §9 + doc-1 §3.4."""
from __future__ import annotations
import pytest
from app.rag.agentic.context_gap_detector import ContextGapDetector
from app.rag.agentic.fallback_chain import FallbackChain


def test_context_gap_detector_detects_insufficient():
    detector = ContextGapDetector()
    assert detector.has_gap("The goal failed because of insufficient context about the topic.") is True

def test_context_gap_detector_passes_valid_answer():
    detector = ContextGapDetector()
    assert detector.has_gap("The deployment was successful at 14:30 UTC.") is False

def test_context_gap_detector_all_12_signals():
    detector = ContextGapDetector()
    signals = [
        "insufficient data", "unclear result", "no information available",
        "cannot determine", "lack of context", "not mentioned in the docs",
        "unknown at this time", "not found", "need more context",
        "more context required", "cannot verify this", "no relevant results",
    ]
    for signal in signals:
        assert detector.has_gap(f"The answer is {signal}"), f"Should detect gap: '{signal}'"

def test_fallback_chain_order_matches_doc2():
    chain = FallbackChain()
    assert chain.FALLBACK_ORDER == ["hybrid", "graph", "hyde", "web", "ltm", "parametric"]

def test_fallback_chain_tracks_attempts():
    chain = FallbackChain()
    chain.record_attempt("hybrid", success=False, reason="low confidence")
    chain.record_attempt("web", success=True, reason="found result")
    assert len(chain.attempts) == 2
    assert chain.final_source == "web"

def test_fallback_chain_parametric_when_all_fail():
    chain = FallbackChain()
    for source in ["hybrid", "graph", "hyde", "web", "ltm"]:
        chain.record_attempt(source, success=False, reason="no results")
    assert chain.final_source == "parametric"

def test_agent_graph_has_node_rag_prime():
    from app.agent.graph import AgentGraph
    assert hasattr(AgentGraph, "_node_rag_prime"), "AgentGraph missing _node_rag_prime"

def test_agent_graph_has_node_rag_remediate():
    from app.agent.graph import AgentGraph
    assert hasattr(AgentGraph, "_node_rag_remediate"), "AgentGraph missing _node_rag_remediate"

def test_agent_graph_has_node_refine():
    from app.agent.graph import AgentGraph
    assert hasattr(AgentGraph, "_node_refine"), "AgentGraph missing _node_refine"

def test_self_refine_pattern_node_name():
    from app.agent.patterns.self_refine import SelfRefinePattern
    p = SelfRefinePattern()
    assert p.node_name == "_node_refine"
