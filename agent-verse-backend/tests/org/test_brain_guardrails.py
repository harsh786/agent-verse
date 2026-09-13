from app.org.autonomy import AutonomyEnforcer
from app.org.brain_guardrails import TickCounters, Verdict, evaluate_guardrails
from app.org.brain_settings import resolve_autonomy_settings
from app.org.brain_types import BrainDecision
from app.org.loop_detector import OrgLoopDetector


def _settings(**over):
    base = {"autonomy": {"daily_budget_usd": 100.0, "per_mission_cost_ceiling_usd": 50.0, **over}}
    return resolve_autonomy_settings(base, monthly_budget_usd=3000.0)


def _decision(**over):
    d = dict(kind="proactive", rationale="advance goal", target_goal="g1",
             est_cost_usd=5.0, risk_level="low", signature="sig-1")
    d.update(over)
    return BrainDecision(**d)


def _counters(**over):
    c = dict(day_spend_usd=0.0, missions_today=0, active_autonomous=0,
             seconds_since_last_launch=100000.0, recent_signatures=frozenset(),
             counters_available=True)
    c.update(over)
    return TickCounters(**c)


def _call(level, **kw):
    return evaluate_guardrails(
        _decision(**kw.pop("decision", {})),
        autonomy_level=level,
        settings=kw.pop("settings", _settings()),
        counters=kw.pop("counters", _counters()),
        kill_switch=kw.pop("kill_switch", False),
        enforcer=AutonomyEnforcer(),
        loop_detector=OrgLoopDetector(),
    )


def test_kill_switch_blocks():
    assert _call(4, kill_switch=True).action == "block"


def test_below_l3_blocks():
    assert _call(2).action == "block"


def test_l3_proposes_low_risk():
    assert _call(3).action == "propose"


def test_l4_executes_low_risk():
    assert _call(4).action == "execute"


def test_cooldown_blocks():
    v = _call(4, counters=_counters(seconds_since_last_launch=10.0),
              settings=_settings(min_interval_seconds=600))
    assert v.action == "block" and "cooldown" in v.reason.lower()


def test_daily_launch_cap_blocks():
    v = _call(4, counters=_counters(missions_today=8), settings=_settings(max_missions_per_day=8))
    assert v.action == "block"


def test_concurrency_cap_blocks():
    v = _call(4, counters=_counters(active_autonomous=2), settings=_settings(max_concurrent=2))
    assert v.action == "block"


def test_duplicate_signature_blocks():
    v = _call(4, counters=_counters(recent_signatures=frozenset({"sig-1"})))
    assert v.action == "block" and "duplicate" in v.reason.lower()


def test_cost_over_ceiling_downgrades_to_propose():
    v = _call(4, decision={"est_cost_usd": 999.0})
    assert v.action == "propose" and "cost" in v.reason.lower()


def test_cost_over_remaining_daily_budget_downgrades():
    v = _call(4, decision={"est_cost_usd": 40.0},
              counters=_counters(day_spend_usd=95.0), settings=_settings(daily_budget_usd=100.0))
    assert v.action == "propose"


def test_high_risk_forces_propose_even_at_l5():
    v = _call(5, decision={"risk_level": "high"})
    assert v.action == "propose" and "approval" in v.reason.lower()


def test_counters_unavailable_fails_closed_to_propose():
    v = _call(4, counters=_counters(counters_available=False))
    assert v.action == "propose"
