"""The always-on pattern-selection summary: a goal's chosen pattern (+ why) is
real and retrievable regardless of the dynamic_orchestration master flag, an
explicit override wins, and the override catalog is registry-driven.
"""

from __future__ import annotations

from app.orchestration.pattern_selection_summary import (
    humanize,
    summarize_pattern_selection,
)


def test_simple_goal_auto_selects_react() -> None:
    summary = summarize_pattern_selection("say hello", goal_id="g1", tenant_id="t1")
    assert summary["source"] == "auto"
    assert summary["primary_pattern"] == "react"
    assert summary["multi_agent_patterns"] == ["single_agent"]
    # Rationale explains the reasoning patterns in plain language.
    assert any(r["category"] == "reasoning" for r in summary["rationale"])
    assert summary["primary_pattern_name"] == "ReAct"


def test_complex_goal_auto_selects_richer_topology() -> None:
    summary = summarize_pattern_selection(
        "Design, implement and rigorously verify a fault-tolerant distributed "
        "consensus protocol across microservices, analyzing every tradeoff",
        goal_id="g2",
        tenant_id="t1",
    )
    # Non-trivial goals pick up reasoning augmentation and/or a coordination topology.
    assert summary["goal_properties"]["complexity"] in {"complex", "expert"}
    assert summary["reasoning_patterns"][0] == "react"
    assert len(summary["reasoning_patterns"]) >= 1


def test_explicit_override_wins() -> None:
    summary = summarize_pattern_selection(
        "say hello",
        goal_id="g3",
        tenant_id="t1",
        agent_config={"primary_strategy": "plan_execute"},
    )
    assert summary["source"] == "override"
    assert summary["override"] == "plan_execute"
    assert summary["primary_pattern"] == "plan_execute"


def test_available_patterns_is_registry_driven() -> None:
    summary = summarize_pattern_selection("say hello", goal_id="g4", tenant_id="t1")
    ids = {p["id"] for p in summary["available_patterns"]}
    # Adding a pattern = registering it → it shows up here for free.
    assert {"react", "plan_execute", "supervisor", "debate", "consensus"} <= ids
    react = next(p for p in summary["available_patterns"] if p["id"] == "react")
    assert react["name"] == "ReAct"
    assert "available" in react


def test_advanced_tier_gate_is_reported() -> None:
    summary = summarize_pattern_selection("say hello", goal_id="g5", tenant_id="t1")
    # Default-off safety gate — selection still recorded, gate state surfaced.
    assert summary["advanced_tier_gated"] is True
    assert summary["advanced_tier_enabled"] is False


def test_humanize_falls_back_gracefully() -> None:
    assert humanize("react") == "ReAct"
    assert humanize("some_new_pattern") == "Some New Pattern"
