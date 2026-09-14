from app.org.brain_decide import OrgSnapshot, decide
from app.org.brain_settings import resolve_autonomy_settings


def _s(**o):
    return resolve_autonomy_settings({"autonomy": o}, monthly_budget_usd=3000.0)


def _plan(goal):
    return (f"advance {goal}", 5.0, "low")


def test_reactive_when_blocked_over_threshold():
    snap = OrgSnapshot(blocked=6, failed=0, active_missions=3, open_goals=[], goals_in_flight=frozenset())
    out = decide(snap, _s(blocked_threshold=5), propose_goal_mission=_plan)
    assert any(d.kind == "reactive" for d in out)


def test_proactive_when_idle_with_open_goal():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=0, open_goals=["g1"], goals_in_flight=frozenset())
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=_plan)
    assert any(d.kind == "proactive" and d.target_goal == "g1" for d in out)


def test_no_proactive_when_goal_already_in_flight():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=0, open_goals=["g1"], goals_in_flight=frozenset({"g1"}))
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=_plan)
    assert all(d.target_goal != "g1" for d in out)


def test_no_proactive_when_busy():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=5, open_goals=["g1"], goals_in_flight=frozenset())
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=_plan)
    assert all(d.kind != "proactive" for d in out)


def test_planner_returning_none_yields_no_proactive():
    snap = OrgSnapshot(blocked=0, failed=0, active_missions=0, open_goals=["g1"], goals_in_flight=frozenset())
    out = decide(snap, _s(idle_threshold=1), propose_goal_mission=lambda g: None)
    assert all(d.kind != "proactive" for d in out)
