"""WS-10 item 2: supervisor/debate must auto-select from a goal's own
characteristics via ONE reachable selector — not only per-agent flags — while
the default-off safety gate keeps the advanced multi-agent tier quiet by default.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.multi_agent_selector import select_multi_agent_patterns
from app.providers.fake import FakeProvider

# ── selector (pure) ───────────────────────────────────────────────────────────


def test_simple_goal_selects_no_multi_agent() -> None:
    sel = select_multi_agent_patterns(
        complexity="simple", domain="operational", multi_step=False, risk="low"
    )
    assert sel.patterns == frozenset()
    assert not sel.supervisor and not sel.debate


def test_complex_technical_multistep_selects_supervisor_only() -> None:
    sel = select_multi_agent_patterns(
        complexity="complex", domain="technical", multi_step=True, risk="low"
    )
    assert sel.supervisor is True
    assert sel.debate is False


def test_expert_analytical_multistep_selects_supervisor_and_debate() -> None:
    sel = select_multi_agent_patterns(
        complexity="expert", domain="analytical", multi_step=True, risk="medium"
    )
    assert sel.supervisor is True
    assert sel.debate is True
    # Reasons are recorded for observability / decision trace.
    assert dict(sel.reasons).keys() >= {"supervisor", "debate"}


def test_expert_high_risk_selects_debate() -> None:
    sel = select_multi_agent_patterns(
        complexity="expert", domain="operational", multi_step=True, risk="critical"
    )
    assert sel.debate is True


def test_critical_risk_selects_consensus_regardless_of_complexity() -> None:
    """Consensus is an irreversible/high-stakes gate keyed purely on risk — a
    'simple' goal with critical risk still warrants independent convergence."""
    sel = select_multi_agent_patterns(
        complexity="simple", domain="operational", multi_step=False, risk="critical"
    )
    assert sel.consensus is True
    assert sel.supervisor is False
    assert sel.debate is False
    assert dict(sel.reasons)["consensus"]


def test_high_risk_alone_does_not_select_consensus() -> None:
    """Only 'critical' triggers consensus; 'high' does not (debate may still apply)."""
    sel = select_multi_agent_patterns(
        complexity="simple", domain="operational", multi_step=False, risk="high"
    )
    assert sel.consensus is False


def test_expert_analytical_not_multi_step_still_selects_debate_not_supervisor() -> None:
    """Supervisor requires multi_step; debate does not — single-step expert/analytical
    goals still warrant adversarial cross-checking even without decomposition."""
    sel = select_multi_agent_patterns(
        complexity="expert", domain="analytical", multi_step=False, risk="low"
    )
    assert sel.debate is True
    assert sel.supervisor is False


def test_selector_is_case_insensitive_on_all_string_inputs() -> None:
    """Mixed-case complexity/domain/risk must still match the lowercase rule sets —
    if case weren't normalized, 'Analytical' wouldn't match _DECOMPOSABLE_DOMAINS
    and supervisor would wrongly stay unselected."""
    sel = select_multi_agent_patterns(
        complexity="EXPERT", domain="Analytical", multi_step=True, risk="CRITICAL"
    )
    assert sel.supervisor is True
    assert sel.debate is True
    assert sel.consensus is True


def test_selector_handles_empty_strings_without_raising() -> None:
    sel = select_multi_agent_patterns(complexity="", domain="", multi_step=True, risk="")
    assert sel.patterns == frozenset()


def test_selector_handles_unrecognized_values_without_raising() -> None:
    sel = select_multi_agent_patterns(
        complexity="nonsense", domain="nonexistent-domain", multi_step=True, risk="unknown"
    )
    assert sel.patterns == frozenset()


def test_moderate_complexity_does_not_select_supervisor() -> None:
    """Only 'complex'/'expert' are ADVANCED_COMPLEXITIES for supervisor decomposition,
    even though 'moderate' is compatible with the ToT pattern elsewhere in the codebase."""
    sel = select_multi_agent_patterns(
        complexity="moderate", domain="technical", multi_step=True, risk="low"
    )
    assert sel.supervisor is False


def test_all_three_patterns_can_be_selected_simultaneously() -> None:
    """A complex/expert, multi-step, analytical, critical-risk goal should stack all
    three signals: supervisor (decompose) + debate (cross-check) + consensus (converge)."""
    sel = select_multi_agent_patterns(
        complexity="expert", domain="analytical", multi_step=True, risk="critical"
    )
    assert sel.patterns == frozenset({"supervisor", "debate", "consensus"})
    reasons = dict(sel.reasons)
    assert reasons.keys() == {"supervisor", "debate", "consensus"}


# ── graph wiring (behavioural) ────────────────────────────────────────────────


def _profile(complexity: str, domain: str, risk: str = "low", multi_step: bool = True) -> Any:
    return SimpleNamespace(
        primary_strategy=SimpleNamespace(strategy_id="react"),
        auxiliary_strategies=(),
        properties=SimpleNamespace(
            complexity=complexity, domain=domain, risk=risk, multi_step=multi_step
        ),
    )


def _graph(**kwargs: Any) -> AgentGraph:
    p = FakeProvider()
    return AgentGraph(planner=p, executor=p, verifier=p, **kwargs)


def _gate(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(agent_auto_multi_agent_enabled=enabled)


def test_complex_goal_auto_routes_to_supervisor_when_gate_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: _gate(True))
    g = _graph(runtime_profile=_profile("expert", "analytical", risk="high"))
    nodes = set(g._graph.get_graph().nodes.keys())
    # Auto-routed WITHOUT any per-agent enable flag.
    assert "supervisor" in nodes
    assert "debate" in nodes
    assert g._auto_multi_agent == frozenset({"supervisor", "debate"})


def test_default_off_gate_keeps_multi_agent_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: _gate(False))
    g = _graph(runtime_profile=_profile("expert", "analytical", risk="high"))
    nodes = set(g._graph.get_graph().nodes.keys())
    assert "supervisor" not in nodes
    assert "debate" not in nodes


def test_simple_goal_does_not_auto_route_even_when_gate_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: _gate(True))
    g = _graph(runtime_profile=_profile("simple", "operational", risk="low", multi_step=False))
    nodes = set(g._graph.get_graph().nodes.keys())
    assert "supervisor" not in nodes
    assert "debate" not in nodes


def test_explicit_flag_overrides_gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per-agent explicit enable still engages the node regardless of auto-selection.
    monkeypatch.setattr("app.core.config.get_settings", lambda: _gate(False))
    g = _graph(enable_supervisor=True)
    assert "supervisor" in set(g._graph.get_graph().nodes.keys())
