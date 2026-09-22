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
    # planner+executor both medium, no faster candidate -> no reroute
    assert resolve(planner=_frontier(), executor=_frontier()).verifier_model == ""


def test_verifier_auto_routes_to_fastest_role_model():
    """Strategy C with NO flag: verification auto-routes to the fastest role model."""
    fast_exec = ModelCapabilityProfile(
        model_id="gpt-4o-mini", parallel_tool_calls=True, latency_tier=LatencyTier.FAST.value
    )
    slow_planner = ModelCapabilityProfile(
        model_id="big-model",
        structured_planning=True,
        strict_schema_enforced=True,
        json_reliability=JsonReliability.HIGH.value,
        latency_tier=LatencyTier.SLOW.value,
    )
    s = resolve(planner=slow_planner, executor=fast_exec, verifier=slow_planner)
    assert s.verifier_model == "gpt-4o-mini"


def test_is_seeded():
    from app.agent.execution_strategy import is_seeded

    assert is_seeded("openai/gpt-oss-20b") is True
    assert is_seeded("claude-opus-5") is True
    assert is_seeded("some-brand-new-model") is False
    assert is_seeded("") is False


def test_all_three_latency_tiers_yield_distinct_fastest_choice():
    """Sweep all three LatencyTier values (FAST/MEDIUM/SLOW) as the planner's
    tier against a SLOW executor+verifier, and confirm the resolver's "fastest
    role model" routing (Strategy C) actually distinguishes all three — not
    just FAST vs SLOW as the existing tests do."""
    slow_exec = ModelCapabilityProfile(model_id="slow-exec", latency_tier=LatencyTier.SLOW.value)
    slow_verifier = ModelCapabilityProfile(
        model_id="slow-verifier", latency_tier=LatencyTier.SLOW.value
    )

    fast_planner = ModelCapabilityProfile(
        model_id="fast-planner", latency_tier=LatencyTier.FAST.value
    )
    s_fast = resolve(planner=fast_planner, executor=slow_exec, verifier=slow_verifier)
    assert s_fast.verifier_model == "fast-planner"

    medium_planner = ModelCapabilityProfile(
        model_id="medium-planner", latency_tier=LatencyTier.MEDIUM.value
    )
    s_medium = resolve(planner=medium_planner, executor=slow_exec, verifier=slow_verifier)
    assert s_medium.verifier_model == "medium-planner"

    slow_planner = ModelCapabilityProfile(
        model_id="slow-planner", latency_tier=LatencyTier.SLOW.value
    )
    s_slow = resolve(planner=slow_planner, executor=slow_exec, verifier=slow_verifier)
    # Nothing is faster than the (also slow) verifier -> no reroute.
    assert s_slow.verifier_model == ""

    # The three outcomes are pairwise distinct, proving the tier ranking
    # (FAST < MEDIUM < SLOW) drives a different selection at every step, not
    # just a binary fast/not-fast split.
    assert s_fast.verifier_model != s_medium.verifier_model
    assert s_medium.verifier_model != s_slow.verifier_model


def test_latency_tier_rank_ordering_is_fast_lt_medium_lt_slow():
    from app.agent.execution_strategy import _TIER_RANK

    assert _TIER_RANK[LatencyTier.FAST.value] < _TIER_RANK[LatencyTier.MEDIUM.value]
    assert _TIER_RANK[LatencyTier.MEDIUM.value] < _TIER_RANK[LatencyTier.SLOW.value]


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
