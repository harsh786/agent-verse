# tests/observability/test_observability_comprehensive.py
"""SSE events, Prometheus metrics, structured logging, OTEL tracing."""
from __future__ import annotations

# ── SSE EVENTS ────────────────────────────────────────────────────────────────

def test_all_9_sse_event_types_defined():
    from app.observability.runtime_decision_trace import SSEEventType
    # SSEEventType is a plain class with string class attributes, not an Enum
    event_types = [
        v for k, v in vars(SSEEventType).items()
        if not k.startswith("_") and isinstance(v, str)
    ]
    assert len(event_types) >= 9


def test_runtime_sse_emitter_pattern_assembled():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter
    emitter = RuntimeSSEEmitter()
    event = emitter.pattern_assembled(
        goal_id="g1",
        complexity="expert",
        risk="high",
        patterns_active={"reasoning": ["raptor", "self_refine"], "safety": ["guardrails"]},
        models={"planner": "gpt-4o"},
        selection_reasons={"raptor": "expert complexity"},
        assembly_latency_ms=12.5,
    )
    assert event["type"] is not None
    assert event["goal_id"] == "g1"
    assert event["complexity"] == "expert"
    assert event["assembly_latency_ms"] == 12.5


def test_runtime_sse_emitter_rag_strategy_selected():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.rag_strategy_selected(
        goal_id="g1",
        strategy="raptor",
        sources=["knowledge_base", "long_term_memory"],
        reranker="rrf",
    )
    assert event["goal_id"] == "g1"
    assert event["type"] == SSEEventType.RAG_STRATEGY_SELECTED
    assert event["strategy"] == "raptor"


def test_runtime_sse_emitter_eval_score_recorded():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.eval_score_recorded(
        goal_id="g1",
        overall_score=0.87,
        scores={"goal_success": 0.9, "rag_quality": 0.85},
    )
    assert event["overall_score"] == 0.87
    assert event["type"] == SSEEventType.EVAL_SCORE_RECORDED
    assert event["scores"]["goal_success"] == 0.9


def test_runtime_sse_emitter_self_improvement_suggested():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.self_improvement_suggested(
        goal_id="g1",
        suggestions=["UPDATE_PROMPT_VARIANT", "STORE_REFLEXION_LESSON"],
    )
    assert "UPDATE_PROMPT_VARIANT" in event["suggestions"]
    assert event["type"] == SSEEventType.SELF_IMPROVEMENT_SUGGESTED


def test_runtime_sse_emitter_guardrail_profile_selected():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.guardrail_profile_selected(
        goal_id="g1",
        bundle="strict",
        scanners=["injection", "pii"],
    )
    assert event["bundle"] == "strict"
    assert event["type"] == SSEEventType.GUARDRAIL_PROFILE_SELECTED
    assert "injection" in event["scanners"]


def test_runtime_sse_emitter_model_route_selected():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.model_route_selected(
        goal_id="g1",
        planner="gpt-4o",
        executor="gpt-4o-mini",
        verifier="gpt-4o-mini",
        cost_class="medium",
    )
    assert event["planner"] == "gpt-4o"
    assert event["type"] == SSEEventType.MODEL_ROUTE_SELECTED


def test_runtime_sse_emitter_runtime_profile_selected():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.runtime_profile_selected(
        goal_id="g1",
        profile_id="p1",
        complexity="expert",
        patterns=["react", "reflection"],
        rag_strategy="raptor",
        assembly_latency_ms=8.5,
    )
    assert event["type"] == SSEEventType.RUNTIME_PROFILE_SELECTED
    assert event["rag_strategy"] == "raptor"


def test_runtime_sse_emitter_chunking_strategy():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.chunking_strategy_selected(
        goal_id="g1",
        content_type="code",
        strategy="ast",
        reason="code content detected",
    )
    assert event["type"] == SSEEventType.CHUNKING_STRATEGY_SELECTED
    assert event["strategy"] == "ast"


def test_runtime_sse_emitter_embedding_strategy():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter, SSEEventType
    emitter = RuntimeSSEEmitter()
    event = emitter.embedding_strategy_selected(
        goal_id="g1",
        model_id="text-embedding-3-small",
        modality="text",
        dimension=1536,
        cost_class="low",
        reason="default text embedding",
    )
    assert event["type"] == SSEEventType.EMBEDDING_STRATEGY_SELECTED
    assert event["dimension"] == 1536


# ── PROMETHEUS METRICS ────────────────────────────────────────────────────────

def test_orchestration_metrics_exist():
    from app.observability.metrics import (
        orchestration_pattern_selected_total,
        orchestration_profile_built_total,
        orchestration_profile_latency_ms,
        orchestration_rag_strategy_total,
        orchestration_readiness_gate_blocked_total,
    )
    assert orchestration_profile_built_total is not None
    assert orchestration_profile_latency_ms is not None
    assert orchestration_pattern_selected_total is not None
    assert orchestration_rag_strategy_total is not None
    assert orchestration_readiness_gate_blocked_total is not None


def test_orchestration_metrics_incrementable():
    from app.observability.metrics import (
        orchestration_profile_built_total,
        orchestration_rag_strategy_total,
    )
    # Must not raise
    orchestration_profile_built_total.labels(
        complexity="expert", risk="high", tenant_plan="professional"
    ).inc()
    orchestration_rag_strategy_total.labels(strategy="raptor").inc()


def test_core_metrics_exist():
    from app.observability.metrics import (
        GOAL_DURATION,
        GOAL_TOTAL,
        TOOL_CALL_TOTAL,
    )
    assert GOAL_DURATION is not None
    assert GOAL_TOTAL is not None
    assert TOOL_CALL_TOTAL is not None


def test_record_goal_duration_helper():
    from app.observability.metrics import record_goal_duration
    # Must not raise; validates label normalization path
    record_goal_duration(status="complete", duration_seconds=1.5, priority="normal")
    record_goal_duration(status="failed", duration_seconds=0.3, priority="high")


def test_record_tool_call_helper():
    from app.observability.metrics import record_tool_call
    record_tool_call("jira.create_issue", "jira", "success", 0.25)
    record_tool_call("unknown_tool", "llm", "failed", 0.01)


def test_record_llm_tokens_helper():
    from app.observability.metrics import record_llm_tokens
    record_llm_tokens("openai", "gpt-4o", "prompt", 100)
    record_llm_tokens("anthropic", "claude-sonnet", "completion", 50)


def test_record_cost_usd_helper():
    from app.observability.metrics import record_cost_usd
    record_cost_usd("llm", 0.002)
    record_cost_usd("goal", 0.01)


# ── STRUCTURED LOGGING ────────────────────────────────────────────────────────

def test_structured_logger_no_exception():
    from app.observability.logging import get_logger
    log = get_logger("test.module")
    # Must not raise
    log.info("test_event", key="value", count=1)
    log.warning("test_warning", error="some error")


def test_structured_logger_returns_bound_logger():
    from app.observability.logging import get_logger
    log = get_logger("test.structured")
    assert log is not None
    # structlog bound loggers support bind()
    bound = log.bind(request_id="req123", tenant_id="t1")
    assert bound is not None


# ── OTEL TRACING ──────────────────────────────────────────────────────────────

def test_tracer_creates_spans():
    from app.observability.tracing import get_tracer
    tracer = get_tracer("test")
    with tracer.start_as_current_span("test_span") as span:
        span.set_attribute("key", "value")
    # Must not raise


def test_tracer_nested_spans():
    from app.observability.tracing import get_tracer
    tracer = get_tracer("test.nested")
    with tracer.start_as_current_span("parent") as parent_span:
        parent_span.set_attribute("level", "parent")
        with tracer.start_as_current_span("child") as child_span:
            child_span.set_attribute("level", "child")
    # Must not raise


# ── COST BREAKDOWN ────────────────────────────────────────────────────────────

def test_cost_breakdown_records():
    from app.observability.cost_breakdown import get_breakdown, record_role_cost
    record_role_cost(
        goal_id="g_cost_test",
        role="planner",
        model="gpt-4o",
        input_tok=100,
        output_tok=50,
        cost=0.01,
    )
    record_role_cost(
        goal_id="g_cost_test",
        role="executor",
        model="gpt-4o-mini",
        input_tok=200,
        output_tok=80,
        cost=0.005,
    )
    breakdown = get_breakdown("g_cost_test")
    assert breakdown is not None
    assert breakdown.total_cost() > 0
    assert abs(breakdown.total_cost() - 0.015) < 1e-9


def test_cost_breakdown_accumulates_same_role():
    from app.observability.cost_breakdown import GoalCostBreakdown
    bd = GoalCostBreakdown(goal_id="g_accum")
    bd.record("planner", "gpt-4o", 100, 50, 0.01)
    bd.record("planner", "gpt-4o", 200, 100, 0.02)
    # Both calls to same role/model should accumulate in one entry
    assert len(bd.entries) == 1
    assert abs(bd.entries[0].cost_usd - 0.03) < 1e-9
    assert bd.entries[0].calls == 2


def test_cost_breakdown_to_dict():
    from app.observability.cost_breakdown import GoalCostBreakdown
    bd = GoalCostBreakdown(goal_id="g_dict")
    bd.record("verifier", "gpt-4o-mini", 50, 20, 0.001)
    d = bd.to_dict()
    assert d["goal_id"] == "g_dict"
    assert "total_cost_usd" in d
    assert "roles" in d
    assert len(d["roles"]) == 1
    assert d["roles"][0]["role"] == "verifier"


def test_finalize_breakdown_removes_from_registry():
    from app.observability.cost_breakdown import (
        _goal_breakdowns,
        finalize_breakdown,
        record_role_cost,
    )
    record_role_cost("g_final", "planner", "gpt-4o", 10, 5, 0.001)
    result = finalize_breakdown("g_final")
    assert result["goal_id"] == "g_final"
    # Should be removed from registry
    assert "g_final" not in _goal_breakdowns
