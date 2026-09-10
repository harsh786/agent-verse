"""The ONE selector owns the whole agent-pattern decision — reasoning *and*
multi-agent topology — generically, registry-gated, nothing hardcoded, and the
multi-agent choice is surfaced in the DecisionTrace.

Covers the WS-10 "fuller wiring" follow-up: PatternSelector.select_agent_patterns
now drives the multi_agent dimension from goal characteristics via the single
shared rule (no ``["single_agent"]`` / ``["goal_tree"]`` hardcode), and
RuntimeProfileBuilder records it as its own trace dimension.
"""

from __future__ import annotations

import pytest

from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import (
    Complexity,
    Domain,
    GoalProperties,
    RiskLevel,
    TimeSensitivity,
)
from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.strategy_registry import build_default_registry


@pytest.fixture
def selector() -> PatternSelector:
    return PatternSelector(registry=build_default_registry())


def _props(**kw: object) -> GoalProperties:
    base: dict[str, object] = dict(
        raw_goal="test goal",
        complexity=Complexity.SIMPLE,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.LOW,
        time_sensitivity=TimeSensitivity.NORMAL,
    )
    base.update(kw)
    return GoalProperties(**base)  # type: ignore[arg-type]


# ── the ONE selector: multi-agent dimension is characteristic-driven ──────────


def test_simple_goal_defaults_to_single_agent(selector: PatternSelector) -> None:
    cfg = selector.select_agent_patterns(_props())
    assert cfg.multi_agent == ["single_agent"]


def test_complex_technical_multistep_selects_supervisor(selector: PatternSelector) -> None:
    props = _props(complexity=Complexity.COMPLEX, domain=Domain.TECHNICAL, multi_step=True)
    cfg = selector.select_agent_patterns(props)
    assert "supervisor" in cfg.multi_agent
    assert "single_agent" not in cfg.multi_agent
    # The reason is recorded for the decision trace / UX.
    assert "supervisor" in cfg.selection_reasons


def test_expert_analytical_selects_goal_tree_supervisor_and_debate(
    selector: PatternSelector,
) -> None:
    props = _props(
        complexity=Complexity.EXPERT,
        domain=Domain.ANALYTICAL,
        risk=RiskLevel.MEDIUM,
        multi_step=True,
        time_sensitivity=TimeSensitivity.BATCH,
    )
    cfg = selector.select_agent_patterns(props)
    assert "goal_tree" in cfg.multi_agent
    assert "supervisor" in cfg.multi_agent
    assert "debate" in cfg.multi_agent


def test_critical_risk_selects_consensus(selector: PatternSelector) -> None:
    props = _props(
        complexity=Complexity.EXPERT,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.CRITICAL,
        multi_step=True,
    )
    cfg = selector.select_agent_patterns(props)
    assert "consensus" in cfg.multi_agent


def test_nothing_hardcoded_registry_gate_removes_unavailable_pattern() -> None:
    """A pattern absent/PLANNED in the registry is never selected — proving the
    decision is registry-driven, not a hardcoded topology."""

    class _NoSupervisorRegistry:
        def __init__(self) -> None:
            self._inner = build_default_registry()

        def is_available(self, sid: str, **kw: object) -> bool:
            if sid == "supervisor":
                return False
            return self._inner.is_available(sid, **kw)

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    selector = PatternSelector(registry=_NoSupervisorRegistry())  # type: ignore[arg-type]
    props = _props(complexity=Complexity.COMPLEX, domain=Domain.TECHNICAL, multi_step=True)
    cfg = selector.select_agent_patterns(props)
    assert "supervisor" not in cfg.multi_agent


# ── the decision is surfaced in the DecisionTrace ─────────────────────────────


async def test_multi_agent_decision_recorded_in_decision_trace() -> None:
    builder = RuntimeProfileBuilder()
    _profile, trace = await builder.build_with_trace(
        "Design and implement a distributed rate limiter across all services, "
        "analyze tradeoffs, and verify correctness under load",
        tenant_id="t1",
        goal_id="g1",
    )
    dims = {d.dimension for d in trace.decisions}
    assert "multi_agent" in dims, f"multi_agent dimension missing from trace: {dims}"
    entry = next(d for d in trace.decisions if d.dimension == "multi_agent")
    assert entry.selector == "PatternSelector"
    assert isinstance(entry.selected, list)
    assert entry.reason  # non-empty plain-language reason
