"""All required SSE events must be emittable from the orchestration layer."""
from __future__ import annotations

import pytest

from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType


def test_emitter_creates_runtime_profile_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.runtime_profile_selected(
        goal_id="g1",
        profile_id="p1",
        complexity="expert",
        patterns=["react", "reflection"],
        rag_strategy="agentic_rag",
        assembly_latency_ms=1.5,
    )
    assert event["type"] == SSEEventType.RUNTIME_PROFILE_SELECTED
    assert event["goal_id"] == "g1"
    assert "patterns" in event
    assert event["assembly_latency_ms"] == 1.5


def test_emitter_creates_rag_strategy_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.rag_strategy_selected(
        goal_id="g1",
        strategy="agentic_rag",
        sources=["knowledge_base", "web_search"],
        reranker="rrf",
    )
    assert event["type"] == SSEEventType.RAG_STRATEGY_SELECTED
    assert "sources" in event


def test_emitter_creates_model_route_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.model_route_selected(
        goal_id="g1",
        planner="gpt-5.2",
        executor="gpt-5.2",
        verifier="gpt-4o-mini",
        cost_class="medium",
    )
    assert event["type"] == SSEEventType.MODEL_ROUTE_SELECTED


def test_emitter_creates_guardrail_profile_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.guardrail_profile_selected(
        goal_id="g1",
        bundle="strict",
        scanners=["injection", "toxicity"],
    )
    assert event["type"] == SSEEventType.GUARDRAIL_PROFILE_SELECTED


def test_emitter_creates_eval_score_recorded_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.eval_score_recorded(
        goal_id="g1",
        overall_score=0.87,
        scores={"goal_success": 1.0, "rag_quality": 0.8, "safety": 1.0},
    )
    assert event["type"] == SSEEventType.EVAL_SCORE_RECORDED
    assert event["overall_score"] == 0.87


def test_emitter_creates_self_improvement_suggested_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.self_improvement_suggested(
        goal_id="g1",
        suggestions=["Consider switching RAG strategy — low confidence"],
    )
    assert event["type"] == SSEEventType.SELF_IMPROVEMENT_SUGGESTED
    assert len(event["suggestions"]) > 0


def test_emitter_creates_pattern_assembled_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.pattern_assembled(
        goal_id="g1",
        complexity="expert",
        risk="low",
        patterns_active={
            "reasoning": ["react", "chain_of_thought"],
            "safety": ["guardrails", "hitl"],
        },
        models={"planner": "gpt-5.2", "executor": "gpt-5.2"},
        selection_reasons={"chain_of_thought": "complexity=expert"},
        assembly_latency_ms=1.2,
    )
    assert event["type"] == SSEEventType.PATTERN_ASSEMBLED
    assert event["complexity"] == "expert"
    assert event["patterns_active"]["reasoning"] == ["react", "chain_of_thought"]
    assert event["assembly_latency_ms"] == 1.2


def test_emitter_creates_chunking_strategy_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.chunking_strategy_selected(
        goal_id="g1",
        content_type="pdf",
        strategy="layout",
        reason="PDF content uses layout-aware chunking",
    )
    assert event["type"] == SSEEventType.CHUNKING_STRATEGY_SELECTED
    assert event["strategy"] == "layout"


def test_emitter_creates_embedding_strategy_selected_event():
    emitter = RuntimeSSEEmitter()
    event = emitter.embedding_strategy_selected(
        goal_id="g1",
        model_id="text-embedding-3-small",
        modality="text",
        dimension=1536,
        cost_class="low",
        reason="content_type=text modality=text",
    )
    assert event["type"] == SSEEventType.EMBEDDING_STRATEGY_SELECTED
    assert event["model_id"] == "text-embedding-3-small"
    assert event["dimension"] == 1536
    assert event["modality"] == "text"
