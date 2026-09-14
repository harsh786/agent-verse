from app.org.autonomy import AutonomyEnforcer
from app.org.brain_guardrails import TickCounters, evaluate_guardrails
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


def _verdict(level, **kw):
    """Convenience wrapper for tests that only care about the Verdict."""
    verdict, _checks = _call(level, **kw)
    return verdict


def test_kill_switch_blocks():
    assert _verdict(4, kill_switch=True).action == "block"


def test_below_l3_blocks():
    assert _verdict(2).action == "block"


def test_l3_proposes_low_risk():
    assert _verdict(3).action == "propose"


def test_l4_executes_low_risk():
    assert _verdict(4).action == "execute"


def test_cooldown_blocks():
    v = _verdict(4, counters=_counters(seconds_since_last_launch=10.0),
                 settings=_settings(min_interval_seconds=600))
    assert v.action == "block" and "cooldown" in v.reason.lower()


def test_daily_launch_cap_blocks():
    v = _verdict(4, counters=_counters(missions_today=8), settings=_settings(max_missions_per_day=8))
    assert v.action == "block"


def test_concurrency_cap_blocks():
    v = _verdict(4, counters=_counters(active_autonomous=2), settings=_settings(max_concurrent=2))
    assert v.action == "block"


def test_duplicate_signature_blocks():
    v = _verdict(4, counters=_counters(recent_signatures=frozenset({"sig-1"})))
    assert v.action == "block" and "duplicate" in v.reason.lower()


def test_cost_over_ceiling_downgrades_to_propose():
    v = _verdict(4, decision={"est_cost_usd": 999.0})
    assert v.action == "propose" and "cost" in v.reason.lower()


def test_cost_over_remaining_daily_budget_downgrades():
    v = _verdict(4, decision={"est_cost_usd": 40.0},
                 counters=_counters(day_spend_usd=95.0), settings=_settings(daily_budget_usd=100.0))
    assert v.action == "propose"


def test_high_risk_forces_propose_even_at_l5():
    v = _verdict(5, decision={"risk_level": "high"})
    assert v.action == "propose" and "approval" in v.reason.lower()


def test_counters_unavailable_fails_closed_to_propose():
    v = _verdict(4, counters=_counters(counters_available=False))
    assert v.action == "propose"


def test_paused_settings_blocks():
    """Guardrail #1 paused branch: settings.paused=True with kill_switch=False blocks."""
    v = _verdict(4, settings=_settings(paused=True), kill_switch=False)
    assert v.action == "block" and "kill switch" in v.reason.lower()


def test_env_kill_switch_blocks(monkeypatch):
    """Guardrail #1 env branch: AV_ORG_AUTONOMY_DISABLED=1 blocks execution."""
    monkeypatch.setenv("AV_ORG_AUTONOMY_DISABLED", "1")
    v = _verdict(4, kill_switch=False)
    assert v.action == "block" and "kill switch" in v.reason.lower()


# ── checks trace (Task 3: Brain Feed v2) ─────────────────────────────────────

_ALL_NAMES = [
    "kill_switch", "autonomy_level", "cooldown", "daily_cap",
    "concurrency", "dedup", "cost", "risk",
]


def test_clean_pass_returns_all_8_checks_passed_in_order():
    verdict, checks = _call(4)
    assert verdict.action == "execute" and verdict.reason == "within policy"
    assert [c.name for c in checks] == _ALL_NAMES
    assert all(c.passed for c in checks)


def test_paused_trace_stops_at_kill_switch_with_only_that_check():
    verdict, checks = _call(4, settings=_settings(paused=True))
    assert verdict.action == "block"
    assert [c.name for c in checks] == ["kill_switch"]
    assert checks[0].passed is False
    assert checks[0].detail == verdict.reason


def test_over_daily_cap_trace_flags_daily_cap_with_real_numbers():
    verdict, checks = _call(
        4, counters=_counters(missions_today=8), settings=_settings(max_missions_per_day=8)
    )
    assert verdict.action == "block"
    assert [c.name for c in checks] == ["kill_switch", "autonomy_level", "cooldown", "daily_cap"]
    assert all(c.passed for c in checks[:-1])
    failing = checks[-1]
    assert failing.name == "daily_cap"
    assert failing.passed is False
    assert failing.detail == verdict.reason
    assert failing.value == "8"
    assert failing.limit == "8"


def test_over_concurrency_trace_flags_concurrency_with_real_numbers():
    verdict, checks = _call(
        4, counters=_counters(active_autonomous=2), settings=_settings(max_concurrent=2)
    )
    assert verdict.action == "block"
    assert [c.name for c in checks] == [
        "kill_switch", "autonomy_level", "cooldown", "daily_cap", "concurrency",
    ]
    assert all(c.passed for c in checks[:-1])
    failing = checks[-1]
    assert failing.name == "concurrency"
    assert failing.passed is False
    assert failing.value == "2"
    assert failing.limit == "2"


def test_cost_over_ceiling_trace_flags_cost_with_real_dollar_amounts():
    verdict, checks = _call(4, decision={"est_cost_usd": 999.0})
    assert verdict.action == "propose"
    assert [c.name for c in checks] == [
        "kill_switch", "autonomy_level", "cooldown", "daily_cap",
        "concurrency", "dedup", "cost",
    ]
    assert all(c.passed for c in checks[:-1])
    failing = checks[-1]
    assert failing.name == "cost"
    assert failing.passed is False
    assert failing.detail == verdict.reason
    assert failing.value == "$999.00"
    assert failing.limit == "$50.00"  # per-mission ceiling from _settings()
