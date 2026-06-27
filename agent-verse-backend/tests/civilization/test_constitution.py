"""Tests for Constitution — pure policy evaluator, zero I/O."""
import pytest
from app.civilization.models import Constitution, SpawnContext, BreachContext
from app.civilization.constitution import evaluate_spawn, evaluate_breach
from app.civilization.models import SpawnDecision


def _default_constitution(**kwargs) -> Constitution:
    defaults = dict(
        max_depth=4, max_total_agents=50, max_concurrent_agents=10,
        total_budget_usd=100.0, per_agent_budget_usd=10.0, budget_decay=0.6,
        spawn_rate_limit_per_min=20, autonomy_ceiling="bounded-autonomous",
    )
    defaults.update(kwargs)
    return Constitution(**defaults)


def _default_ctx(**kwargs) -> SpawnContext:
    defaults = dict(
        civilization_id="civ-1", tenant_id="t1", requester_agent_id="a1",
        requested_capability="jira_search", goal_text="search issues",
        depth=1, current_total_agents=5, current_concurrent_agents=3,
        civilization_budget_spent_usd=20.0, spawn_rate_last_min=5,
        parent_budget_usd=10.0, parent_policy_ids=[],
    )
    defaults.update(kwargs)
    return SpawnContext(**defaults)


def test_spawn_approved_within_limits():
    verdict = evaluate_spawn(_default_ctx(), _default_constitution())
    assert verdict.decision == SpawnDecision.APPROVED


def test_spawn_denied_at_max_depth():
    verdict = evaluate_spawn(_default_ctx(depth=4), _default_constitution(max_depth=4))
    assert verdict.decision == SpawnDecision.DENIED
    assert "depth" in verdict.reason


def test_spawn_denied_at_max_total_agents():
    verdict = evaluate_spawn(
        _default_ctx(current_total_agents=50),
        _default_constitution(max_total_agents=50),
    )
    assert verdict.decision == SpawnDecision.DENIED
    assert "total_agents" in verdict.reason


def test_spawn_denied_at_max_concurrent():
    verdict = evaluate_spawn(
        _default_ctx(current_concurrent_agents=10),
        _default_constitution(max_concurrent_agents=10),
    )
    assert verdict.decision == SpawnDecision.DENIED
    assert "concurrent" in verdict.reason


def test_spawn_denied_at_rate_limit():
    verdict = evaluate_spawn(
        _default_ctx(spawn_rate_last_min=20),
        _default_constitution(spawn_rate_limit_per_min=20),
    )
    assert verdict.decision == SpawnDecision.DENIED
    assert "spawn_rate" in verdict.reason


def test_spawn_denied_insufficient_budget():
    # parent_budget=10, decay=0.6, depth=1 → child_budget=6; remaining=0.5 → denied
    verdict = evaluate_spawn(
        _default_ctx(parent_budget_usd=10.0, civilization_budget_spent_usd=99.5),
        _default_constitution(total_budget_usd=100.0, budget_decay=0.6),
    )
    assert verdict.decision == SpawnDecision.DENIED
    assert "budget" in verdict.reason


def test_budget_decay_math():
    c = _default_constitution(per_agent_budget_usd=10.0, budget_decay=0.6)
    assert abs(c.compute_child_budget(10.0, 1) - 6.0) < 0.001
    assert abs(c.compute_child_budget(10.0, 2) - 3.6) < 0.001
    assert abs(c.compute_child_budget(10.0, 3) - 2.16) < 0.001


def test_autonomy_clamped_to_ceiling():
    c = _default_constitution(autonomy_ceiling="bounded-autonomous")
    verdict = evaluate_spawn(_default_ctx(), c)
    assert verdict.clamped_autonomy == "bounded-autonomous"


def test_autonomy_clamped_when_ceiling_is_supervised():
    c = _default_constitution(autonomy_ceiling="supervised")
    verdict = evaluate_spawn(_default_ctx(), c)
    assert verdict.clamped_autonomy == "supervised"


def test_inherited_policies_merged():
    c = _default_constitution()
    c.inherited_policy_ids = ["p1", "p2"]
    ctx = _default_ctx(parent_policy_ids=["p3"])
    verdict = evaluate_spawn(ctx, c)
    assert "p1" in verdict.inherited_policy_ids
    assert "p2" in verdict.inherited_policy_ids
    assert "p3" in verdict.inherited_policy_ids


def test_inherited_policies_deduped():
    c = _default_constitution()
    c.inherited_policy_ids = ["p1", "p2"]
    ctx = _default_ctx(parent_policy_ids=["p1", "p3"])
    verdict = evaluate_spawn(ctx, c)
    assert verdict.inherited_policy_ids.count("p1") == 1


def test_verdict_snapshot_includes_key_fields():
    verdict = evaluate_spawn(_default_ctx(), _default_constitution())
    assert "depth" in verdict.snapshot
    assert "total_agents" in verdict.snapshot
    assert "max_depth" in verdict.snapshot


def test_verdict_snapshot_includes_budget_fields():
    verdict = evaluate_spawn(_default_ctx(), _default_constitution())
    assert "child_budget_computed" in verdict.snapshot
    assert "total_budget_usd" in verdict.snapshot
    assert "civilization_budget_spent_usd" in verdict.snapshot


def test_approved_verdict_has_positive_budget():
    verdict = evaluate_spawn(_default_ctx(), _default_constitution())
    assert verdict.allowed_budget_usd > 0.0


def test_denied_verdict_has_zero_budget():
    verdict = evaluate_spawn(_default_ctx(depth=4), _default_constitution(max_depth=4))
    assert verdict.allowed_budget_usd == 0.0


def test_multiple_violations_all_reported():
    # Exceed both depth and total_agents
    verdict = evaluate_spawn(
        _default_ctx(depth=4, current_total_agents=50),
        _default_constitution(max_depth=4, max_total_agents=50),
    )
    assert verdict.decision == SpawnDecision.DENIED
    assert "depth" in verdict.reason
    assert "total_agents" in verdict.reason


def test_breach_budget_exhausted():
    ctx = BreachContext(
        civilization_id="c1", tenant_id="t1",
        budget_spent_usd=100.0, budget_total_usd=100.0,
        spawn_rate_last_min=5, total_agents=10, concurrent_agents=3,
    )
    verdict = evaluate_breach(ctx, _default_constitution())
    assert verdict.breached is True
    assert any("budget" in r for r in verdict.reasons)


def test_breach_spawn_rate_exceeded():
    ctx = BreachContext(
        civilization_id="c1", tenant_id="t1",
        budget_spent_usd=10.0, budget_total_usd=100.0,
        spawn_rate_last_min=25, total_agents=10, concurrent_agents=3,
    )
    verdict = evaluate_breach(ctx, _default_constitution(spawn_rate_limit_per_min=20))
    assert verdict.breached is True
    assert any("spawn rate" in r for r in verdict.reasons)


def test_breach_total_agents_exceeded():
    ctx = BreachContext(
        civilization_id="c1", tenant_id="t1",
        budget_spent_usd=10.0, budget_total_usd=100.0,
        spawn_rate_last_min=5, total_agents=51, concurrent_agents=3,
    )
    verdict = evaluate_breach(ctx, _default_constitution(max_total_agents=50))
    assert verdict.breached is True
    assert any("total agents" in r for r in verdict.reasons)


def test_no_breach_within_limits():
    ctx = BreachContext(
        civilization_id="c1", tenant_id="t1",
        budget_spent_usd=50.0, budget_total_usd=100.0,
        spawn_rate_last_min=5, total_agents=10, concurrent_agents=3,
    )
    verdict = evaluate_breach(ctx, _default_constitution())
    assert verdict.breached is False
    assert verdict.reasons == []


def test_constitution_from_dict_round_trip():
    c = _default_constitution()
    d = c.to_dict()
    c2 = Constitution.from_dict(d)
    assert c2.max_depth == c.max_depth
    assert c2.total_budget_usd == c.total_budget_usd
    assert c2.budget_decay == c.budget_decay


def test_constitution_from_dict_ignores_unknown_keys():
    d = {
        "max_depth": 3,
        "total_budget_usd": 50.0,
        "unknown_future_field": "ignored",
    }
    c = Constitution.from_dict(d)
    assert c.max_depth == 3
    assert c.total_budget_usd == 50.0


def test_constitution_defaults():
    c = Constitution()
    assert c.max_depth == 4
    assert c.max_total_agents == 50
    assert c.max_concurrent_agents == 10
    assert c.total_budget_usd == 100.0
    assert c.budget_decay == 0.6
    assert c.reputation_floor == 0.2
    assert c.min_viable_roster == 1
