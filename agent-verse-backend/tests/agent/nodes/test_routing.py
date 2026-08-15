"""Unit tests for the RoutingMixin — _route and _route_after_execute."""
from __future__ import annotations

import pytest

from app.providers.fake import FakeProvider  # type: ignore[import]
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus
from app.tenancy.context import TenantContext


def _make_graph() -> AgentGraph:
    provider = FakeProvider()
    return AgentGraph(planner=provider, executor=provider, verifier=provider)


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="test-tenant", plan="starter", api_key_id="test-key")


def _state(goal: str = "test goal") -> dict:
    tenant = _tenant()
    agent_state = AgentState(goal=goal, tenant_ctx=tenant)
    return dict(goal=goal, tenant_ctx=tenant, agent_state=agent_state, iteration=0)


# ─── _route ──────────────────────────────────────────────────────────────────


def test_route_complete_when_verification_success():
    g = _make_graph()
    state = _state()
    state["agent_state"].verification_success = True
    result = g._route(state)
    assert result == "complete"


def test_route_max_iter_when_no_agent_state():
    g = _make_graph()
    state = {"goal": "x", "tenant_ctx": _tenant()}
    result = g._route(state)
    assert result == "max_iter"


def test_route_max_iter_on_guardrail_rejected():
    g = _make_graph()
    state = _state()
    state["terminal_reason"] = "guardrail_rejected"
    result = g._route(state)
    assert result == "max_iter"


def test_route_max_iter_on_max_iterations():
    g = _make_graph()
    state = _state()
    state["agent_state"].verification_success = False
    state["agent_state"].context["verification_retry"] = True
    state["iteration"] = g._max_iterations + 1
    result = g._route(state)
    assert result == "max_iter"
    assert state["agent_state"].status == GoalStatus.FAILED


def test_route_replan_on_verification_failure():
    g = _make_graph()
    state = _state()
    state["agent_state"].verification_success = False
    state["agent_state"].context["verification_retry"] = True
    state["iteration"] = 1
    result = g._route(state)
    assert result in ("replan", "reflect", "rag_remediate")  # valid next nodes


def test_route_max_iter_when_retry_false():
    g = _make_graph()
    state = _state()
    state["agent_state"].verification_success = False
    state["agent_state"].context["verification_retry"] = False
    result = g._route(state)
    assert result == "max_iter"
    assert state["agent_state"].status == GoalStatus.FAILED


def test_route_stagnation_detected():
    g = _make_graph()
    state = _state()
    state["agent_state"].verification_success = False
    state["agent_state"].context["verification_retry"] = True
    # Same feedback 3 times in a row
    repeated = "missing required output field"
    state["agent_state"].context["_feedback_history"] = [repeated, repeated, repeated]
    state["agent_state"].verification_feedback = repeated
    state["iteration"] = 2
    result = g._route(state)
    assert result == "max_iter"
    assert "stagnated" in (state["agent_state"].error_message or "").lower()


# ─── _route_after_execute ─────────────────────────────────────────────────────


def test_route_after_execute_continue_on_success():
    g = _make_graph()
    state = _state()
    state["agent_state"].status = GoalStatus.EXECUTING
    result = g._route_after_execute(state)
    assert result == "continue"


def test_route_after_execute_failed_on_failed_status():
    g = _make_graph()
    state = _state()
    state["agent_state"].status = GoalStatus.FAILED
    result = g._route_after_execute(state)
    assert result == "failed"


# ─── _max_reflection_rounds ──────────────────────────────────────────────────


def test_max_reflection_rounds_default():
    g = _make_graph()
    assert g._max_reflection_rounds() >= 0
    assert g._max_reflection_rounds() <= 2
