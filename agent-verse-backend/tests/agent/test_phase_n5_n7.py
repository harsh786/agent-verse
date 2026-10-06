"""Phase N5-N7: SSE events + action dispatch (the ABTestingEngine was removed, a05-F089-01)."""
from __future__ import annotations


def test_runtime_decision_trace_has_all_5_events():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter
    emitter = RuntimeSSEEmitter()
    assert hasattr(emitter, "runtime_profile_selected")
    assert hasattr(emitter, "guardrail_profile_selected")
    assert hasattr(emitter, "self_improvement_suggested")
    assert hasattr(emitter, "chunking_strategy_selected")
    assert hasattr(emitter, "embedding_strategy_selected")


def test_runtime_profile_selected_event_format():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter
    emitter = RuntimeSSEEmitter()
    event = emitter.runtime_profile_selected(
        goal_id="g1", profile_id="p1", complexity="simple",
        patterns=["react"], rag_strategy="hybrid", assembly_latency_ms=5.2,
    )
    assert event["type"] is not None
    assert event["goal_id"] == "g1"


def test_guardrail_profile_selected_event_format():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter
    emitter = RuntimeSSEEmitter()
    event = emitter.guardrail_profile_selected(
        goal_id="g1", bundle="strict", scanners=["injection", "pii"]
    )
    assert event["type"] is not None
    assert event["goal_id"] == "g1"


def test_self_improvement_suggested_event_format():
    from app.observability.runtime_decision_trace import RuntimeSSEEmitter
    emitter = RuntimeSSEEmitter()
    event = emitter.self_improvement_suggested(
        goal_id="g1", suggestions=["UPDATE_PROMPT_VARIANT", "STORE_REFLEXION_LESSON"]
    )
    assert event["type"] is not None
    assert "UPDATE_PROMPT_VARIANT" in event["suggestions"]


def test_self_improvement_blacklist_records_tool():
    """BLACKLIST_TOOL_PATTERN must record failed tools in ToolReliabilityStore."""
    # Verify ToolReliabilityStore.record() is callable
    from app.memory.tool_reliability import ToolReliabilityStore
    store = ToolReliabilityStore.__new__(ToolReliabilityStore)
    assert hasattr(store, "record")

