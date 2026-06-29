"""Tests for civilization domain models — pure data types."""
from __future__ import annotations

import pytest

from app.civilization.models import (
    BreachContext,
    BreachVerdict,
    CivilizationStatus,
    Constitution,
    LearningStatus,
    MemberStatus,
    MetaAgentConfigValidated,
    SpawnContext,
    SpawnDecision,
    SpawnVerdict,
)


# ── Enums ─────────────────────────────────────────────────────────────────────


def test_civilization_status_values():
    assert CivilizationStatus.ACTIVE == "active"
    assert CivilizationStatus.PAUSED == "paused"
    assert CivilizationStatus.RETIRED == "retired"


def test_member_status_values():
    assert MemberStatus.ACTIVE == "active"
    assert MemberStatus.IDLE == "idle"
    assert MemberStatus.RETIRED == "retired"
    assert MemberStatus.SPAWNING == "spawning"
    assert MemberStatus.DEBATING == "debating"
    assert MemberStatus.FAILED == "failed"


def test_spawn_decision_values():
    assert SpawnDecision.APPROVED == "approved"
    assert SpawnDecision.DENIED == "denied"


def test_learning_status_values():
    assert LearningStatus.CANDIDATE == "candidate"
    assert LearningStatus.VALIDATED == "validated"
    assert LearningStatus.PROMOTED == "promoted"
    assert LearningStatus.REJECTED == "rejected"


# ── Constitution ──────────────────────────────────────────────────────────────


def test_constitution_defaults():
    c = Constitution()
    assert c.max_depth == 4
    assert c.max_total_agents == 50
    assert c.max_concurrent_agents == 10
    assert c.total_budget_usd == 100.0
    assert c.per_agent_budget_usd == 10.0
    assert c.budget_decay == 0.6
    assert c.spawn_rate_limit_per_min == 20
    assert c.high_risk_requires_hitl is True
    assert c.inherited_policy_ids == []
    assert c.autonomy_ceiling == "bounded-autonomous"
    assert c.reputation_floor == 0.2
    assert c.idle_ttl_seconds == 3600
    assert c.min_viable_roster == 1


def test_constitution_custom_fields():
    c = Constitution(max_depth=2, total_budget_usd=500.0, reputation_floor=0.3)
    assert c.max_depth == 2
    assert c.total_budget_usd == 500.0
    assert c.reputation_floor == 0.3


def test_constitution_from_dict_round_trip():
    c = Constitution(max_depth=3, total_budget_usd=75.0, budget_decay=0.5)
    d = c.to_dict()
    c2 = Constitution.from_dict(d)
    assert c2.max_depth == 3
    assert c2.total_budget_usd == 75.0
    assert c2.budget_decay == 0.5


def test_constitution_from_dict_ignores_unknown_keys():
    d = {"max_depth": 5, "total_budget_usd": 200.0, "future_unknown_field": "ignored"}
    c = Constitution.from_dict(d)
    assert c.max_depth == 5
    assert c.total_budget_usd == 200.0


def test_constitution_to_dict_contains_all_fields():
    c = Constitution()
    d = c.to_dict()
    assert "max_depth" in d
    assert "max_total_agents" in d
    assert "total_budget_usd" in d
    assert "budget_decay" in d
    assert "inherited_policy_ids" in d
    assert "autonomy_ceiling" in d


def test_constitution_compute_child_budget_depth_1():
    c = Constitution(budget_decay=0.6)
    result = c.compute_child_budget(10.0, 1)
    assert abs(result - 6.0) < 0.001


def test_constitution_compute_child_budget_depth_2():
    c = Constitution(budget_decay=0.6)
    result = c.compute_child_budget(10.0, 2)
    assert abs(result - 3.6) < 0.001


def test_constitution_compute_child_budget_depth_0():
    c = Constitution(budget_decay=0.6)
    result = c.compute_child_budget(10.0, 0)
    assert abs(result - 10.0) < 0.001


def test_constitution_inherited_policy_ids_default_empty():
    c = Constitution()
    assert c.inherited_policy_ids == []
    # Mutating one should not affect another
    c.inherited_policy_ids.append("p1")
    c2 = Constitution()
    assert c2.inherited_policy_ids == []


# ── SpawnContext ──────────────────────────────────────────────────────────────


def test_spawn_context_creation():
    ctx = SpawnContext(
        civilization_id="civ-1",
        tenant_id="t1",
        requester_agent_id="a1",
        requested_capability="jira_search",
        goal_text="Find P1 issues",
        depth=2,
        current_total_agents=5,
        current_concurrent_agents=3,
        civilization_budget_spent_usd=20.0,
        spawn_rate_last_min=3,
        parent_budget_usd=10.0,
        parent_policy_ids=["p1"],
    )
    assert ctx.civilization_id == "civ-1"
    assert ctx.depth == 2
    assert ctx.parent_policy_ids == ["p1"]


def test_spawn_context_default_parent_policy_ids():
    ctx = SpawnContext(
        civilization_id="civ-1",
        tenant_id="t1",
        requester_agent_id="a1",
        requested_capability="x",
        goal_text="y",
        depth=0,
        current_total_agents=0,
        current_concurrent_agents=0,
        civilization_budget_spent_usd=0.0,
        spawn_rate_last_min=0,
        parent_budget_usd=5.0,
    )
    assert ctx.parent_policy_ids == []


# ── SpawnVerdict ──────────────────────────────────────────────────────────────


def test_spawn_verdict_approved():
    v = SpawnVerdict(
        decision=SpawnDecision.APPROVED,
        reason="within limits",
        allowed_budget_usd=5.0,
        clamped_autonomy="bounded-autonomous",
        inherited_policy_ids=["p1"],
        snapshot={"depth": 1},
    )
    assert v.decision == SpawnDecision.APPROVED
    assert v.allowed_budget_usd == 5.0
    assert v.inherited_policy_ids == ["p1"]


def test_spawn_verdict_denied():
    v = SpawnVerdict(decision=SpawnDecision.DENIED, reason="depth exceeded")
    assert v.decision == SpawnDecision.DENIED
    assert v.allowed_budget_usd == 0.0
    assert v.snapshot == {}


# ── BreachContext & BreachVerdict ─────────────────────────────────────────────


def test_breach_context_creation():
    ctx = BreachContext(
        civilization_id="civ-1",
        tenant_id="t1",
        budget_spent_usd=90.0,
        budget_total_usd=100.0,
        spawn_rate_last_min=25,
        total_agents=55,
        concurrent_agents=12,
    )
    assert ctx.budget_spent_usd == 90.0
    assert ctx.total_agents == 55


def test_breach_verdict_defaults():
    v = BreachVerdict(breached=False)
    assert v.breached is False
    assert v.reasons == []


def test_breach_verdict_with_reasons():
    v = BreachVerdict(breached=True, reasons=["budget exhausted", "rate exceeded"])
    assert v.breached is True
    assert len(v.reasons) == 2


# ── MetaAgentConfigValidated ──────────────────────────────────────────────────


def test_meta_agent_config_validated_creation():
    cfg = MetaAgentConfigValidated(
        name="JiraAgent",
        goal_template="Handle Jira tasks",
        autonomy_mode="bounded-autonomous",
        connector_ids=["jira-connector"],
        trigger_config={"schedule": "daily"},
        system_prompt="You are a Jira expert",
        max_iterations=10,
        allowed_collection_ids=["col-1"],
        policy_ids=["p1"],
    )
    assert cfg.name == "JiraAgent"
    assert cfg.autonomy_mode == "bounded-autonomous"
    assert cfg.eval_suite_id is None


def test_meta_agent_config_with_eval_suite():
    cfg = MetaAgentConfigValidated(
        name="Agent",
        goal_template="Do tasks",
        autonomy_mode="supervised",
        connector_ids=[],
        trigger_config={},
        system_prompt="",
        max_iterations=5,
        allowed_collection_ids=[],
        policy_ids=[],
        eval_suite_id="suite-123",
    )
    assert cfg.eval_suite_id == "suite-123"
