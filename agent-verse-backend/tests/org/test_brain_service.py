"""OrgBrain.run_tick — SENSE/DECIDE/GUARD/ACT/NARRATE orchestration (Task 6).

Fully faked OrgService/counters/store/planner — no DB or Redis needed. Pins
the three autonomy-level behaviors: L1 no-op, L3 propose-only, L4 execute.
"""

from __future__ import annotations

import pytest

from app.org.brain import OrgBrain


class _FakeMission:
    def __init__(self, mid="m1", status="active"):
        self.id, self.status = mid, status


class _FakeOrgService:
    def __init__(self, health):
        self._health = health
        self.created, self.executed = [], []

    async def get_org_health(self, org_id):
        return self._health

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


def _brain(org_service, counters):
    from app.org.autonomy import AutonomyEnforcer
    from app.org.loop_detector import OrgLoopDetector

    return OrgBrain(
        org_service=org_service, brain_store=_FakeStore(), counters=counters,
        enforcer=AutonomyEnforcer(), loop_detector=OrgLoopDetector(),
        planner=lambda goal, ctx: (f"advance {goal}", 2.0, "low"),
    )


@pytest.mark.asyncio
async def test_l4_idle_org_executes_a_proactive_mission():
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    counters = _FakeCounters()
    brain = _brain(svc, counters)
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=4,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["executed"] == 1 and svc.executed and counters.launches == 1


@pytest.mark.asyncio
async def test_l3_idle_org_only_proposes():
    svc = _FakeOrgService({"task_counts": {"blocked": 0, "failed": 0}})
    brain = _brain(svc, _FakeCounters())
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=3,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out["proposed"] == 1 and svc.created and svc.created[0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_l1_org_is_noop():
    svc = _FakeOrgService({"task_counts": {"blocked": 9, "failed": 9}})
    brain = _brain(svc, _FakeCounters())
    out = await brain.run_tick(org_id="o1", tenant_id="t1", autonomy_level=1,
                               org_settings={}, monthly_budget_usd=3000.0,
                               org_goals=["g1"], org_mission="grow")
    assert out == {"proposed": 0, "executed": 0, "blocked": 1} or out["executed"] == 0
    assert not svc.created and not svc.executed
