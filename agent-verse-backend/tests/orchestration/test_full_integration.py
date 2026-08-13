"""Full pipeline: GoalClassifier → PatternAssembler → PatternConfig.to_sse_event()."""
from __future__ import annotations

from app.agent.dynamic_graph import DynamicGraphAssembler
from app.agent.goal_classifier import goal_classifier
from app.agent.pattern_assembler import pattern_assembler
from app.agent.pattern_config import Complexity, RiskLevel
from app.providers.fake import FakeProvider


def test_full_classify_assemble_pipeline():
    goal = "delete all records from the production database permanently"
    props = goal_classifier.classify_fast(goal)
    assert props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    cfg = pattern_assembler.assemble(props, agent_config={})
    assert "hitl" in cfg.safety_patterns
    assert cfg.autonomy_mode == "supervised"
    event = cfg.to_sse_event("test_goal_id")
    assert event["type"] == "pattern_assembled"
    assert "hitl" in event["patterns_active"]["safety"]


def test_full_pipeline_simple_goal():
    goal = "list all open tickets"
    props = goal_classifier.classify_fast(goal)
    assert props.complexity == Complexity.SIMPLE
    cfg = pattern_assembler.assemble(props, agent_config={})
    assert "react" in cfg.reasoning_patterns
    assert cfg.autonomy_mode == "bounded-autonomous"
    assert "hitl" not in cfg.safety_patterns


def test_dynamic_graph_assembler_wires_graph():
    goal = "research AI safety techniques comprehensively"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    assembler = DynamicGraphAssembler()
    provider = FakeProvider()
    graph = assembler.assemble(cfg, planner=provider, executor=provider, verifier=provider)
    assert graph is not None


def test_pattern_config_has_goal_properties_attached():
    goal = "delete prod db"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    assert cfg.goal_properties is not None
    assert cfg.goal_properties.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_assembly_latency_under_5ms():
    import time

    goal = "list open tickets"
    t0 = time.perf_counter()
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 5.0, f"Full classify+assemble took {elapsed:.2f}ms — should be < 5ms"
