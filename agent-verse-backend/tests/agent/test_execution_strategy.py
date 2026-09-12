"""Model-capability-aware execution strategy resolver (P1)."""

from __future__ import annotations

from app.agent.execution_strategy import (
    ExecutionStrategy,
    JsonReliability,
    LatencyTier,
    ModelCapabilityProfile,
    PlanMode,
    ToolMode,
    latency_tier_for_ms,
    profile_for,
    resolve,
)


def _frontier(model_id: str = "frontier") -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        model_id=model_id,
        structured_planning=True,
        parallel_tool_calls=True,
        json_reliability=JsonReliability.HIGH.value,
        strict_schema_enforced=True,
        latency_tier=LatencyTier.MEDIUM.value,
    )


def _weak(model_id: str = "weak") -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        model_id=model_id,
        json_reliability=JsonReliability.MEDIUM.value,
        latency_tier=LatencyTier.SLOW.value,
    )


# ── static profile lookup ────────────────────────────────────────────────────


def test_profile_for_known_families():
    assert profile_for("openai/gpt-oss-20b").structured_planning is False
    assert profile_for("claude-opus-5").structured_planning is True
    assert profile_for("gpt-5.2").structured_planning is True
    assert profile_for("gpt-4o-mini").latency_tier == LatencyTier.FAST.value


def test_profile_for_unknown_is_conservative():
    p = profile_for("some-random-model")
    assert p.structured_planning is False
    assert p.parallel_tool_calls is False
    assert p.json_reliability == JsonReliability.LOW.value


def test_profile_for_empty():
    p = profile_for("")
    assert p.model_id == "unknown"
    assert p.structured_planning is False


def test_latency_tier_derivation():
    assert latency_tier_for_ms(500) == LatencyTier.FAST.value
    assert latency_tier_for_ms(5000) == LatencyTier.MEDIUM.value
    assert latency_tier_for_ms(20000) == LatencyTier.SLOW.value
    assert latency_tier_for_ms(0) == LatencyTier.MEDIUM.value


# ── resolver: Strategy A (plan mode) ─────────────────────────────────────────


def test_frontier_gets_structured_plan():
    s = resolve(planner=_frontier(), executor=_frontier())
    assert s.plan_mode == PlanMode.STRUCTURED
    assert s.wave_width_cap == 5


def test_weak_planner_stays_sequential():
    s = resolve(planner=_weak(), executor=_weak())
    assert s.plan_mode == PlanMode.SEQUENTIAL
    # smaller wave cap for non-high reliability
    assert s.wave_width_cap == 3


def test_structured_requires_reliability_not_just_capability():
    # capable but neither strict-schema nor high json reliability -> sequential
    p = ModelCapabilityProfile(
        model_id="risky",
        structured_planning=True,
        json_reliability=JsonReliability.MEDIUM.value,
        strict_schema_enforced=False,
    )
    assert resolve(planner=p, executor=p).plan_mode == PlanMode.SEQUENTIAL


# ── resolver: Strategy B (tool mode) ─────────────────────────────────────────


def test_parallel_tools_when_executor_supports():
    s = resolve(planner=_weak(), executor=_frontier())
    assert s.tool_mode == ToolMode.PARALLEL
    # planner still weak -> sequential plan even with parallel tools
    assert s.plan_mode == PlanMode.SEQUENTIAL


def test_single_tool_when_executor_weak():
    assert resolve(planner=_frontier(), executor=_weak()).tool_mode == ToolMode.SINGLE


# ── resolver: Strategy C (latency routing) ───────────────────────────────────


def test_verifier_routed_to_fast_model():
    s = resolve(planner=_frontier(), executor=_frontier(), fast_model_id="gpt-4o-mini")
    assert s.verifier_model == "gpt-4o-mini"


def test_no_verifier_reroute_without_fast_model():
    assert resolve(planner=_frontier(), executor=_frontier()).verifier_model == ""


def test_no_verifier_reroute_when_already_fast():
    fast_v = ModelCapabilityProfile(model_id="gpt-4o-mini", latency_tier=LatencyTier.FAST.value)
    s = resolve(planner=_frontier(), executor=_frontier(), verifier=fast_v,
                fast_model_id="gpt-4o-mini")
    assert s.verifier_model == ""


# ── mixed / fallback ─────────────────────────────────────────────────────────


def test_gpt_oss_end_to_end_is_safe_default():
    p = profile_for("openai/gpt-oss-20b")
    s = resolve(planner=p, executor=p)
    assert s.plan_mode == PlanMode.SEQUENTIAL
    assert s.tool_mode == ToolMode.SINGLE


def test_safe_default_constructor():
    s = ExecutionStrategy.safe_default()
    assert s.plan_mode == PlanMode.SEQUENTIAL
    assert s.tool_mode == ToolMode.SINGLE
