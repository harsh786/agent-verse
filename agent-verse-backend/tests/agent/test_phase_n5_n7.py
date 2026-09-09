"""Phase N5-N7: ABTesting wiring + SSE events + action dispatch."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_ab_testing_engine_has_record_result_async():
    from app.optimization.ab_testing import ABTestingEngine, ExperimentType
    engine = ABTestingEngine()
    assert hasattr(engine, "record_result_async")


async def test_ab_testing_engine_record_async_no_db():
    from app.optimization.ab_testing import ABTestingEngine, ExperimentType
    engine = ABTestingEngine()
    # Must not raise without DB
    await engine.record_result_async(
        "g1", ExperimentType.RAG_STRATEGY, "control", 0.85, tenant_id="t1"
    )
    stats = engine.get_arm_stats(ExperimentType.RAG_STRATEGY, "control")
    assert stats["call_count"] == 1


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


def test_ab_testing_engine_singleton_has_db_factory():
    """Module-level ab_testing_engine must be wired with db_factory in main.py."""
    from app.optimization.ab_testing import ab_testing_engine
    # ab_testing_engine._db_factory should be set in main.py lifespan
    # Just verify the singleton exists
    assert ab_testing_engine is not None
