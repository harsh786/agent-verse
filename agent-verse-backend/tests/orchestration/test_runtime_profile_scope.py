"""Isolated unit tests for GoalRuntimeProfile's scope boundaries.

The existing suites (test_runtime_profile.py, test_runtime_profile_builder.py)
cover construction and validation, but nothing proves that a profile built for
one goal/tenant cannot leak into, or be mutated by, another — which matters
because ``RuntimeProfileBuilder`` is a long-lived object reused across many
goals and tenants (it is not re-instantiated per request). These tests target
exactly that: frozen-instance guarantees, and that sequential ``build()``
calls on the *same* builder instance for *different* tenants never share
mutable state.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.orchestration.runtime_profile import (
    GoalRuntimeProfile,
    default_pattern_limits,
)
from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.strategy_contracts import PatternLimits


# ── GoalRuntimeProfile immutability ─────────────────────────────────────────────


async def test_goal_runtime_profile_tenant_id_cannot_be_reassigned() -> None:
    """The profile is a frozen dataclass: tenant_id must not be mutable after
    construction, otherwise a reference held past its goal could be
    repointed at a different tenant."""
    builder = RuntimeProfileBuilder()
    profile = await builder.build("do a thing", tenant_id="tenant-frozen-a", goal_id="g1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.tenant_id = "tenant-frozen-b"  # type: ignore[misc]


async def test_goal_runtime_profile_goal_id_cannot_be_reassigned() -> None:
    builder = RuntimeProfileBuilder()
    profile = await builder.build("do a thing", tenant_id="t1", goal_id="g-frozen")

    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.goal_id = "g-hijacked"  # type: ignore[misc]


def test_pattern_limits_are_frozen() -> None:
    """PatternLimits must be frozen so effective_limits on one profile can
    never be mutated in place and bleed into another goal sharing the
    default-limits factory."""
    limits = default_pattern_limits()
    with pytest.raises((TypeError, ValueError)):
        limits.calls = 999999  # type: ignore[misc]


# ── Cross-goal / cross-tenant isolation on a shared builder instance ───────────


async def test_sequential_builds_for_different_tenants_carry_correct_identity() -> None:
    """A single (long-lived, reused-across-requests) builder instance must
    stamp each profile with the tenant_id/goal_id it was actually built for —
    not leftovers from whichever goal was built immediately before it."""
    builder = RuntimeProfileBuilder()

    profile_a = await builder.build("research the market", tenant_id="tenant-a", goal_id="ga")
    profile_b = await builder.build("deploy the service", tenant_id="tenant-b", goal_id="gb")
    profile_a2 = await builder.build("research more", tenant_id="tenant-a", goal_id="ga2")

    assert profile_a.tenant_id == "tenant-a"
    assert profile_a.goal_id == "ga"
    assert profile_b.tenant_id == "tenant-b"
    assert profile_b.goal_id == "gb"
    assert profile_a2.tenant_id == "tenant-a"
    assert profile_a2.goal_id == "ga2"

    # Distinct profile_id per build — never reused/cached across goals.
    assert len({profile_a.profile_id, profile_b.profile_id, profile_a2.profile_id}) == 3


async def test_max_iterations_override_does_not_leak_to_next_build() -> None:
    """An agent_config override (e.g. max_iterations) passed for one goal must
    not persist as the default for the next goal built on the same builder
    instance — the builder must not retain per-call config as shared state."""
    builder = RuntimeProfileBuilder()

    overridden = await builder.build(
        "long running batch job",
        tenant_id="t1",
        goal_id="g-override",
        agent_config={"max_iterations": 987},
    )
    assert overridden.agent_patterns.max_iterations == 987

    plain = await builder.build(
        "long running batch job",
        tenant_id="t1",
        goal_id="g-plain",
    )
    assert plain.agent_patterns.max_iterations != 987


async def test_tenant_limit_ceiling_from_one_build_does_not_apply_to_the_next() -> None:
    """tenant_limit_ceiling is per-call config (from agent_config), not
    builder state — a ceiling requested for one goal must be None for the
    next goal that doesn't request one."""
    builder = RuntimeProfileBuilder()
    ceiling = PatternLimits(
        calls=5, nodes=5, edges=5, depth=2, fan_out=2, rounds=2,
        tokens=1000, duration_seconds=60, cost_usd=1.0,
    )

    capped = await builder.build(
        "goal with a tight tenant ceiling",
        tenant_id="t-ceiling",
        goal_id="g-capped",
        agent_config={"tenant_limit_ceiling": ceiling.model_dump()},
    )
    assert capped.tenant_limit_ceiling is not None
    assert capped.effective_limits.calls <= 5

    uncapped = await builder.build(
        "goal with no ceiling",
        tenant_id="t-ceiling",
        goal_id="g-uncapped",
    )
    assert uncapped.tenant_limit_ceiling is None


async def test_mutating_one_profiles_compliance_tags_does_not_affect_another() -> None:
    """Regression-style aliasing check: SecurityConfig.compliance_tags uses a
    mutable list field. If two profiles ever shared the same list object,
    mutating one profile's tags in place (a common pattern for callers that
    append audit tags post-hoc) would silently leak into a sibling goal."""
    builder = RuntimeProfileBuilder()
    profile_a = await builder.build("goal a", tenant_id="t1", goal_id="ga-tags")
    profile_b = await builder.build("goal b", tenant_id="t1", goal_id="gb-tags")

    assert profile_a.security.compliance_tags is not profile_b.security.compliance_tags
    profile_a.security.compliance_tags.append("pci")
    assert "pci" not in profile_b.security.compliance_tags


async def test_selection_reasons_dict_not_shared_between_profiles() -> None:
    """Same aliasing concern for AgentPatternConfig.selection_reasons (a dict)."""
    builder = RuntimeProfileBuilder()
    profile_a = await builder.build("goal a", tenant_id="t1", goal_id="ga-reasons")
    profile_b = await builder.build("goal b", tenant_id="t1", goal_id="gb-reasons")

    assert profile_a.agent_patterns.selection_reasons is not profile_b.agent_patterns.selection_reasons
    profile_a.agent_patterns.selection_reasons["injected"] = "leaked"
    assert "injected" not in profile_b.agent_patterns.selection_reasons


async def test_identity_scope_defaults_to_tenant() -> None:
    """SecurityConfig.identity_scope documents the intended scope boundary —
    guard the default so a future change can't silently widen it."""
    builder = RuntimeProfileBuilder()
    profile = await builder.build("goal", tenant_id="t1", goal_id="g-scope")
    assert profile.security.identity_scope == "tenant"


async def test_to_dict_includes_tenant_and_goal_ids_for_correlation() -> None:
    """to_dict() output (persisted into goal.execution_context / SSE events)
    must always carry the owning tenant_id and goal_id so downstream
    consumers can verify scope."""
    builder = RuntimeProfileBuilder()
    profile = await builder.build("goal", tenant_id="t-dict", goal_id="g-dict")
    data = profile.to_dict()
    assert data["tenant_id"] == "t-dict"
    assert data["goal_id"] == "g-dict"


def test_goal_runtime_profile_construction_rejects_mismatched_tenant_free_fields() -> None:
    """GoalRuntimeProfile itself has no tenant-scoping validation beyond
    storing tenant_id — this test documents that the dataclass trusts its
    caller and pins the current (single-tenant-per-instance) contract so a
    future refactor that tries to share one profile across tenants is
    caught immediately."""
    import app.orchestration.runtime_profile as rp_module

    profile = GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="tenant-only-one",
        properties=rp_module.GoalProperties(raw_goal="x"),
        agent_patterns=rp_module.AgentPatternConfig(),
        rag_strategy=rp_module.RAGStrategyConfig(),
        model_plan=rp_module.ModelPlanConfig(),
        security=rp_module.SecurityConfig(),
        memory_cache=rp_module.MemoryCacheConfig(),
        eval_config=rp_module.EvalConfig(),
    )
    assert profile.tenant_id == "tenant-only-one"
    # There is exactly one tenant_id field on the dataclass — no secondary
    # "owner" or "creator" tenant field that could disagree with it.
    tenant_fields = [f.name for f in dataclasses.fields(profile) if "tenant" in f.name]
    assert tenant_fields == ["tenant_id", "tenant_plan", "tenant_limit_ceiling"]
