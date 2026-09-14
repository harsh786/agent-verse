"""OrgBrain.run_tick — SENSE/DECIDE/GUARD/ACT/NARRATE orchestration (Task 6).

Fully faked OrgService/counters/store/planner — no DB or Redis needed. Pins
the three autonomy-level behaviors: L1 no-op, L3 propose-only, L4 execute.

Also covers the final consolidated fixes:
  * Fix 1 — SENSE now feeds DECIDE/GUARD real ``goals_in_flight``/
    ``recent_signatures`` derived from in-flight autonomous missions (via
    ``OrgService.list_missions``), instead of the hardcoded empty sets that
    let duplicate missions launch.
  * Fix 3 — the "execute" branch persists the mission as "planned" and
    dispatches it via an injected ``dispatcher`` (mirroring
    ``execute_org_mission.apply_async`` in production) instead of calling
    ``create_mission_and_execute`` in-process (which has no wired
    ``app_state``/``GoalService`` in the Celery beat loop and used to strand
    the mission at "planned" until the 3-minute resweep).
"""

from __future__ import annotations

import pytest

from app.org.brain import OrgBrain


class _FakeMission:
    def __init__(self, mid="m1", status="active", trigger_event=None):
        self.id, self.status = mid, status
        self.trigger_event = trigger_event or {}


class _FakeOrgService:
    def __init__(self, health, in_flight=None):
        self._health = health
        self._in_flight = in_flight or []
        self.created, self.executed = [], []

    async def get_org_health(self, org_id):
        return self._health

    async def list_missions(self, org_id, **kw):
        return self._in_flight

    async def create_mission(self, **kw):
        self.created.append(kw)
        return _FakeMission(status=kw.get("status", "active"))

    async def create_mission_and_execute(self, **kw):
        self.executed.append(kw)
        return _FakeMission(), {"dispatched": True}


class _FakeCounters:
    def __init__(self, **snap):
        self._snap = snap
        self.launches = 0

    async def snapshot(self):
        return (self._snap.get("spend", 0.0), self._snap.get("count", 0), self._snap.get("since", 1e9))

    async def record_launch(self, est_cost_usd):
        self.launches += 1


class _FakeStore:
    def __init__(self):
        self.rows = []

    async def record(self, **kw):
        self.rows.append(kw)
        return "d1"


class _FakeDispatcher:
    def __init__(self):
        self.calls = []

    def __call__(self, kwargs):
        self.calls.append(kwargs)


def _brain(org_service, counters, dispatcher=None):
    from app.org.autonomy import AutonomyEnforcer
    from app.org.loop_detector import OrgLoopDetector

    return OrgBrain(
        org_service=org_service, brain_store=_FakeStore(), counters=counters,
        enforcer=AutonomyEnforcer(), loop_detector=OrgLoopDetector(),
        planner=lambda goal, ctx: (f"advance {goal}", 2.0, "low"),
        dispatcher=dispatcher,
    )


@pytest.mark.asyncio
async def test_l4_idle_org_executes_a_proactive_mission():
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    counters = _FakeCounters()
    dispatcher = _FakeDispatcher()
    brain = _brain(svc, counters, dispatcher=dispatcher)
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=4,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["executed"] == 1 and counters.launches == 1
    assert svc.created and svc.created[0]["status"] == "planned"
    assert svc.created[0]["source"] == "autonomous"
    # target_goal + signature are stamped so a future tick's SENSE can dedup.
    assert svc.created[0]["trigger_event"]["target_goal"] == "g1"
    assert svc.created[0]["trigger_event"]["signature"] == "proactive:g1"
    # Fix 3: dispatched on the wired path, not left stranded on "planned".
    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert call["mission_id"] == "m1"
    assert call["org_id"] == "o1" and call["tenant_id"] == "t1"
    assert call["autonomy_level"] == 4


@pytest.mark.asyncio
async def test_l4_execute_without_dispatcher_does_not_crash():
    """No dispatcher wired (defensive default) -> mission still persists as
    'planned'; nothing dispatched, but the tick doesn't blow up. Production
    always wires a real dispatcher (app/scaling/tasks.py); this only proves
    the None-default is safe."""
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    counters = _FakeCounters()
    brain = _brain(svc, counters, dispatcher=None)
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=4,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["executed"] == 1
    assert svc.created and svc.created[0]["status"] == "planned"


@pytest.mark.asyncio
async def test_l3_idle_org_only_proposes():
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    brain = _brain(svc, _FakeCounters())
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=3,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["proposed"] == 1 and svc.created and svc.created[0]["status"] == "proposed"
    assert svc.created[0]["trigger_event"]["target_goal"] == "g1"


@pytest.mark.asyncio
async def test_l1_org_is_noop():
    svc = _FakeOrgService({"task_counts": {"blocked": 9, "failed": 9}})
    brain = _brain(svc, _FakeCounters())
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=1,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out == {"proposed": 0, "executed": 0, "blocked": 1} or out["executed"] == 0
    assert not svc.created and not svc.executed


@pytest.mark.asyncio
async def test_l4_idle_org_does_not_relaunch_goal_with_active_autonomous_mission():
    """Fix 1 — DECIDE-level dedup: an ACTIVE autonomous mission already
    targeting 'g1' (stamped via trigger_event, exactly as this brain creates
    them) means the proactive loop skips 'g1' entirely — no new mission,
    no decision at all for it."""
    active_mission = _FakeMission(
        mid="m-active", status="active",
        trigger_event={"target_goal": "g1", "signature": "proactive:g1"},
    )
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}}, in_flight=[active_mission])
    counters = _FakeCounters()
    brain = _brain(svc, counters)
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=4,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out == {"proposed": 0, "executed": 0, "blocked": 0}
    assert not svc.created and counters.launches == 0


@pytest.mark.asyncio
async def test_l4_reactive_duplicate_blocked_by_recent_signature():
    """Fix 1 — GUARD-level dedup: a recent/active autonomous mission with
    signature 'reactive:blocked' means the SAME reactive decision this tick
    (blocked tasks still over threshold) is deduped by GUARD check #6, rather
    than launching a second remediation mission."""
    active_mission = _FakeMission(
        mid="m-active", status="active",
        trigger_event={"target_goal": "", "signature": "reactive:blocked"},
    )
    svc = _FakeOrgService({"task_counts": {"blocked": 9, "failed": 0}}, in_flight=[active_mission])
    counters = _FakeCounters()
    brain = _brain(svc, counters)
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=4,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=[], org_mission="grow")
    assert out["blocked"] == 1
    assert not svc.created and not svc.executed and counters.launches == 0
