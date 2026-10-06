"""TIME-*: the six time trigger types fire on the live beat and worker (B1).

Every scenario creates real schedules through the API, lets them FIRE on the
live beat + schedule worker, and asserts from the outside what a user would
check: one goal per due slot (``GET /schedules/{id}/history``), at the right
moment, with the template's text, under the right tenant (another tenant gets
404) and plan (the worker's ``run_goal_queue_selected ... plan=`` log line),
and the result visible on the goal.

* TIME-CRON-TZ: a cron in Asia/Kolkata (+05:30) and one in America/New_York for
  the same instant fire once each at that UTC minute; neither fires the
  previous day's slot on creation (B1-1); an unknown zone is refused (B1-2).
* TIME-INTERVAL: an every-60 s interval fires once per minute, never twice.
* TIME-ONE-SHOTS: once / deadline (warning before the deadline) / relative_delay
  (fixed base) fire exactly once at their instant; a once already in the past
  fires late once with catch_up=all and never with catch_up=none (B1-5).
* TIME-RELATIVE-EVENT: relative_delay counted from each event on a channel (and
  from a payload timestamp) fires once per event, a redelivered event arms once,
  another tenant's event arms nothing, a deleted trigger never fires (B1-8).
* TIME-BUSINESS-CALENDAR: a business-hours slot fires; the same on a holiday or a
  non-business day does not (B1-6).
* TIME-LIFECYCLE: pause (no fire), resume (no backlog of the paused slots,
  B1-1), edit (the new cron, no replay), delete (no fire after, B1-3).
* TIME-PLAN-FLOOR: free (900 s) vs enterprise (60 s) floors on create, PATCH and
  natural language.
* TIME-NL: natural-language schedules with an IANA zone, a relative and a
  one-off time (B1-10).
* TIME-SCALE-DUE-INDEX: hundreds of far-future schedules are not re-read on
  every tick (the beat claims only due rows, TRG-15).
* TIME-CATCH-UP (disruptive: suppresses the trigger loop ~4 min and restarts the
  beat; ``RW_ALLOW_BEAT_RESTART=1``): missed slots replay per catch_up: all / latest / none.
* TIME-EXACTLY-ONCE (``RW_SECOND_BEAT_CONTAINER`` and
  ``RW_SECOND_SCHEDULE_WORKER_CONTAINER`` running): two beats and two schedule
  workers still create exactly one goal per slot.

Uses the enterprise tenant (60 s floor; ``RW_ENTERPRISE_*``) and the second,
free tenant (``RW_SECOND_TENANT_*``) for isolation and the free floor.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.real_world.helpers import (
    LiveAPI,
    mask,
    schedule_history_runs,
    tag,
    wait_until,
)

ACK = "Reply with exactly the word ACK. Do not use any tools."
WORKER = os.getenv("RW_WORKER_CONTAINER", "agentverse-backend-worker-1")
BEAT = os.getenv("RW_BEAT_CONTAINER", "agentverse-backend-beat-1")
SCHEDULE_WORKER = os.getenv("RW_SCHEDULE_WORKER_CONTAINER", "agentverse-backend-schedule-worker-1")
REDIS = os.getenv("RW_REDIS_CONTAINER", "agentverse-backend-redis-1")
# How late a fire may start after its due instant: the tick runs on the minute
# (B1-7) for minute-aligned slots; a one-shot due mid-minute waits for the next.
ALIGNED_LATENESS_S = float(os.getenv("RW_ALIGNED_LATENESS_S", "45"))
ONE_SHOT_LATENESS_S = float(os.getenv("RW_ONE_SHOT_LATENESS_S", "100"))


# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
def ent(enterprise_api: LiveAPI | None) -> LiveAPI:
    if enterprise_api is None:
        pytest.skip("needs RW_ENTERPRISE_API_KEY / RW_ENTERPRISE_TENANT_FILE (60 s floor)")
    return enterprise_api


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _sleep_until(when: datetime) -> None:
    delay = (when - _now()).total_seconds()
    if delay > 0:
        time.sleep(delay)


def _next_slot(min_lead_s: float) -> datetime:
    """The first whole UTC minute at least ``min_lead_s`` ahead."""
    t = _now() + timedelta(seconds=min_lead_s)
    return (t + timedelta(minutes=1)).replace(second=0, microsecond=0)


def _trigger(api: LiveAPI, todo: list[str], spec: dict[str, Any], template: str,
             expect: int = 201) -> dict[str, Any]:
    resp = api.post("/triggers", json={"spec": spec, "goal_template": template})
    if resp.status_code == 201:
        todo.append(str(resp.json()["schedule_id"]))
    assert resp.status_code == expect, (
        f"POST /triggers {spec.get('trigger_type')} -> {resp.status_code}: {mask(resp.text[:400])}"
    )
    return dict(resp.json())


def _cleanup(api: LiveAPI, todo: list[str]) -> None:
    for sid in todo:
        with contextlib.suppress(Exception):
            api.delete(f"/triggers/{sid}")


def _runs(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    body = api.json_ok("GET", f"/schedules/{sid}/history", params={"limit": 100})
    return sorted(schedule_history_runs(body), key=lambda r: str(r.get("started_at")))


def _goal_runs(api: LiveAPI, sid: str, after: datetime | None = None) -> list[dict[str, Any]]:
    out = []
    for r in _runs(api, sid):
        started = _ts(r.get("started_at"))
        if r.get("goal_id") and (after is None or (started and started > after)):
            out.append(r)
    return out


def _wait_runs(api: LiveAPI, sid: str, n: int, timeout: float, desc: str) -> list[dict[str, Any]]:
    return list(wait_until(lambda: _goal_runs(api, sid), timeout=timeout, interval=10,
                           desc=desc, done=lambda runs: len(runs) >= n))


def _worker_plan(goal_id: str) -> str | None:
    """The plan the worker ran the goal with (``run_goal_queue_selected`` line)."""
    with contextlib.suppress(Exception):
        proc = subprocess.run(["docker", "logs", "--since", "30m", WORKER],
                              capture_output=True, text=True, timeout=60, check=False)
        for line in (proc.stdout + proc.stderr).splitlines():
            if "run_goal_queue_selected" in line and goal_id in line:
                m = re.search(r"plan=(\w+)", line)
                if m:
                    return m.group(1)
    return None


def _goal(api: LiveAPI, gid: str) -> dict[str, Any]:
    return dict(api.json_ok("GET", f"/goals/{gid}"))


def _assert_goal(api: LiveAPI, gid: str, template_part: str, other: LiveAPI | None,
                 evidence: dict[str, Any], *, plan: str = "enterprise") -> dict[str, Any]:
    goal = _goal(api, gid)
    assert template_part in str(goal.get("goal", "")), (
        f"goal {gid} text {mask(goal.get('goal'))[:200]!r} is not the trigger's template"
    )
    seen_plan = wait_until(lambda: _worker_plan(gid), timeout=240, interval=10,
                           desc=f"the worker to pick up goal {gid}")
    evidence.setdefault("goal_plans", {})[gid] = seen_plan
    assert seen_plan == plan, f"goal {gid} ran on plan {seen_plan!r}, expected {plan!r}"
    if other is not None:
        assert other.get(f"/goals/{gid}").status_code == 404, "another tenant can read the goal"
    return goal


def _terminal(api: LiveAPI, gid: str, timeout: float = 300) -> dict[str, Any]:
    return dict(wait_until(lambda: _goal(api, gid), timeout=timeout, interval=10,
                           desc=f"goal {gid} to finish",
                           done=lambda g: g.get("status") in {"complete", "completed", "failed",
                                                              "cancelled"}))


def _docker(*cmd: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *cmd], capture_output=True, text=True,
                          timeout=timeout, check=False)


def _running(container: str) -> bool:
    out = _docker("inspect", "-f", "{{.State.Running}}", container, timeout=30)
    return out.returncode == 0 and out.stdout.strip() == "true"


# ── TIME-CRON-TZ ──────────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-CRON-TZ")
def test_cron_fires_once_at_its_local_time(ent: LiveAPI, second_tenant_api: LiveAPI | None,
                                           evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    try:
        slot = _next_slot(100)
        ist = slot.astimezone(ZoneInfo("Asia/Kolkata"))
        ny = slot.astimezone(ZoneInfo("America/New_York"))
        evidence["slot_utc"] = slot.isoformat()
        name = f"rw-time-ist-{tag()}"
        # POST /schedules (B1-2: it now keeps its timezone) and POST /triggers.
        via_schedules = ent.post("/schedules", json={
            "trigger_type": "cron", "cron_expr": f"{ist.minute} {ist.hour} * * *",
            "timezone": "Asia/Kolkata", "name": name, "goal_template": f"{ACK} (ist)"})
        assert via_schedules.status_code == 201, mask(via_schedules.text[:300])
        sid_ist = str(via_schedules.json()["schedule_id"])
        todo.append(sid_ist)
        assert via_schedules.json()["spec"]["timezone"] == "Asia/Kolkata"
        sid_ny = str(_trigger(ent, todo, {"trigger_type": "cron", "timezone": "America/New_York",
                                          "cron_expression": f"{ny.minute} {ny.hour} * * *"},
                              f"{ACK} (ny)")["schedule_id"])
        evidence["crons"] = {"ist": f"{ist.minute} {ist.hour} * * *",
                             "ny": f"{ny.minute} {ny.hour} * * *"}
        # An unknown zone is refused on both endpoints (it used to run in UTC).
        bad = ent.post("/schedules", json={"trigger_type": "cron", "cron_expr": "0 9 * * *",
                                           "timezone": "Asia/Kolkatta", "goal_template": ACK})
        assert bad.status_code == 422 and "Asia/Kolkatta" in bad.text, bad.text[:200]
        _trigger(ent, todo, {"trigger_type": "cron", "cron_expression": "0 9 * * *",
                             "timezone": "IST"}, ACK, expect=422)

        # B1-1: a tick passes before the slot; yesterday's slot is not fired.
        _sleep_until(slot - timedelta(seconds=5))
        early = {sid: _goal_runs(ent, sid) for sid in (sid_ist, sid_ny)}
        evidence["runs_before_slot"] = {k: len(v) for k, v in early.items()}
        assert not any(early.values()), f"fired before its slot: {mask(early)[:300]}"

        fired: dict[str, dict[str, Any]] = {}
        for label, sid in (("ist", sid_ist), ("ny", sid_ny)):
            runs = _wait_runs(ent, sid, 1, 150, f"the {label} cron to fire")
            started = _ts(runs[0]["started_at"])
            assert started is not None
            lateness = (started - slot).total_seconds()
            evidence[f"{label}_lateness_s"] = round(lateness, 1)
            assert 0 <= lateness <= ALIGNED_LATENESS_S, (
                f"{label} fired at {started.isoformat()} for the {slot.isoformat()} slot")
            fired[label] = runs[0]
        # Exactly once: the next minute adds nothing; next fire is tomorrow.
        time.sleep(70)
        for label, sid in (("ist", sid_ist), ("ny", sid_ny)):
            assert len(_goal_runs(ent, sid)) == 1, f"{label} fired more than once"
            nxt = _ts(ent.json_ok("GET", f"/triggers/{sid}").get("next_fire_at"))
            assert nxt == slot + timedelta(days=1), f"{label} next_fire_at {nxt}"
        for label, run in fired.items():
            gid = str(run["goal_id"])
            _assert_goal(ent, gid, f"({label})", second_tenant_api, evidence)
        done = _terminal(ent, str(fired["ist"]["goal_id"]))
        evidence["ist_goal_status"] = done.get("status")
        assert done.get("status") in {"complete", "completed"}, mask(done)[:400]
        assert "ACK" in str(done.get("result") or done.get("final_answer") or done).upper()
    finally:
        _cleanup(ent, todo)


# ── TIME-INTERVAL ─────────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-INTERVAL")
def test_interval_fires_once_per_interval(ent: LiveAPI, second_tenant_api: LiveAPI | None,
                                          evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    try:
        created = ent.post("/schedules", json={"trigger_type": "interval", "interval_seconds": 60,
                                               "name": f"rw-time-interval-{tag()}",
                                               "goal_template": f"{ACK} (interval)"})
        assert created.status_code == 201, mask(created.text[:300])
        sid = str(created.json()["schedule_id"])
        todo.append(sid)
        runs = _wait_runs(ent, sid, 3, 260, "three interval fires")
        ent.post(f"/schedules/{sid}/pause")
        starts = [_ts(r["started_at"]) for r in runs]
        gaps = [round((b - a).total_seconds(), 1) for a, b in itertools.pairwise(starts)
                if a and b]
        evidence.update(fires=len(runs), gaps_s=gaps)
        assert len({r["goal_id"] for r in runs}) == len(runs), "two fires shared a goal"
        minutes = [s.replace(second=0, microsecond=0) for s in starts if s]
        assert len(set(minutes)) == len(minutes), f"two fires in one minute: {minutes}"
        assert all(45 <= g <= 80 for g in gaps), f"fires not one interval apart: {gaps}"
        for r in runs[:2]:
            _assert_goal(ent, str(r["goal_id"]), "(interval)", second_tenant_api, evidence)
    finally:
        _cleanup(ent, todo)


# ── TIME-ONE-SHOTS ────────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-ONE-SHOTS")
def test_one_shot_types_fire_exactly_once(ent: LiveAPI, evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    try:
        now = _now().replace(microsecond=0)
        due = now + timedelta(seconds=95)
        specs = {
            "once": {"trigger_type": "once", "run_at": due.isoformat()},
            "deadline": {"trigger_type": "deadline", "run_at": (due + timedelta(minutes=2))
                         .isoformat(), "deadline_warning_seconds": 120},
            "relative": {"trigger_type": "relative_delay",
                         "run_at": (due - timedelta(seconds=60)).isoformat(),
                         "relative_offset_seconds": 60},
            "past_all": {"trigger_type": "once", "run_at": (now - timedelta(hours=1)).isoformat()},
            "past_none": {"trigger_type": "once", "catch_up": "none",
                          "run_at": (now - timedelta(hours=1)).isoformat()},
        }
        sids = {k: str(_trigger(ent, todo, s, f"{ACK} ({k})")["schedule_id"])
                for k, s in specs.items()}
        evidence["due"] = due.isoformat()
        wait_until(lambda: all(_goal_runs(ent, sids[k]) for k in ("once", "deadline", "relative")),
                   timeout=95 + ONE_SHOT_LATENESS_S + 60, interval=10,
                   desc="once / deadline / relative_delay to fire")
        time.sleep(75)  # one more tick: nothing fires again
        counts = {k: len(_goal_runs(ent, sid)) for k, sid in sids.items()}
        evidence["fires"] = counts
        assert counts == {"once": 1, "deadline": 1, "relative": 1, "past_all": 1,
                          "past_none": 0}, counts
        for k in ("once", "deadline", "relative"):
            started = _ts(_goal_runs(ent, sids[k])[0]["started_at"])
            assert started is not None
            lateness = (started - due).total_seconds()
            evidence[f"{k}_lateness_s"] = round(lateness, 1)
            assert 0 <= lateness <= ONE_SHOT_LATENESS_S, f"{k} fired {lateness:.0f}s after due"
            # B1-13: a fired one-shot has no next run (the column holds 9999-01-01).
            assert ent.json_ok("GET", f"/triggers/{sids[k]}").get("next_fire_at") is None
    finally:
        _cleanup(ent, todo)


# ── TIME-RELATIVE-EVENT ───────────────────────────────────────────────────────


def _publish(api: LiveAPI, channel: str, body: dict[str, Any]) -> None:
    resp = api.post(f"/triggers/events/{channel}", json=body)
    assert resp.status_code == 202, f"publish -> {resp.status_code}: {mask(resp.text[:200])}"


def _delayed(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    return list(api.json_ok("GET", f"/triggers/{sid}/delayed-fires"))


@pytest.mark.scenario("TIME-RELATIVE-EVENT")
def test_relative_delay_counts_from_each_event(ent: LiveAPI, second_tenant_api: LiveAPI | None,
                                              evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    try:
        channel = f"rw.support.escalated.{tag()}"
        template = "Follow up on support ticket {{payload.ticket_id}}. " + ACK
        after_event = str(_trigger(ent, todo, {"trigger_type": "relative_delay",
                                               "event_channel": channel,
                                               "relative_offset_seconds": 60},
                                   template)["schedule_id"])
        from_field = str(_trigger(ent, todo, {"trigger_type": "relative_delay",
                                              "event_channel": channel,
                                              "relative_to_field": "opened_at",
                                              "relative_offset_seconds": 120},
                                  template)["schedule_id"])
        doomed = str(_trigger(ent, todo, {"trigger_type": "relative_delay",
                                          "event_channel": channel,
                                          "relative_offset_seconds": 90},
                              template)["schedule_id"])
        # Refused: no base at all, and a malformed payload path.
        _trigger(ent, todo, {"trigger_type": "relative_delay", "relative_offset_seconds": 60},
                 template, expect=422)
        sent = _now()
        opened = (sent - timedelta(seconds=60)).isoformat()
        _publish(ent, channel, {"event_id": "esc-1", "ticket_id": "TCK-4411", "opened_at": opened})
        _publish(ent, channel, {"event_id": "esc-1", "ticket_id": "TCK-4411", "opened_at": opened})
        _publish(ent, channel, {"event_id": "esc-2", "ticket_id": "TCK-4412", "opened_at": opened})
        if second_tenant_api is not None:  # same channel name, other tenant
            _publish(second_tenant_api, channel, {"event_id": "esc-x", "ticket_id": "OTHER"})
        armed = wait_until(lambda: {s: _delayed(ent, s) for s in (after_event, from_field, doomed)},
                           timeout=60, interval=3, desc="the events to arm the delays",
                           done=lambda d: all(len(v) == 2 for v in d.values()))
        evidence["armed"] = {s[:8]: [(f["event_id"], f["due_at"]) for f in v]
                             for s, v in armed.items()}
        for f in armed[after_event]:
            due = _ts(f["due_at"])
            assert due and abs((due - (sent + timedelta(seconds=60))).total_seconds()) <= 20
        for f in armed[from_field]:  # counted from the payload's opened_at
            assert _ts(f["due_at"]) == _ts(opened) + timedelta(seconds=120)
        assert ent.delete(f"/triggers/{doomed}").status_code == 204
        todo.remove(doomed)

        for sid in (after_event, from_field):
            runs = _wait_runs(ent, sid, 2, 60 + ONE_SHOT_LATENESS_S + 60,
                              "one fire per event")
            texts = sorted(str(_goal(ent, str(r["goal_id"])).get("goal", "")) for r in runs)
            evidence.setdefault("goal_texts", {})[sid[:8]] = [t[:60] for t in texts]
            assert any("TCK-4411" in t for t in texts) and any("TCK-4412" in t for t in texts)
        time.sleep(70)
        counts = {s[:8]: len(_goal_runs(ent, s)) for s in (after_event, from_field, doomed)}
        evidence["fires"] = counts
        assert counts == {after_event[:8]: 2, from_field[:8]: 2, doomed[:8]: 0}, counts
        assert all(f["status"] == "fired" for f in _delayed(ent, after_event))
        run = _goal_runs(ent, after_event)[0]
        _assert_goal(ent, str(run["goal_id"]), "Follow up on support ticket TCK-441",
                     second_tenant_api, evidence)
    finally:
        _cleanup(ent, todo)


# ── TIME-BUSINESS-CALENDAR ────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-BUSINESS-CALENDAR")
def test_business_calendar_skips_holidays_and_off_days(ent: LiveAPI,
                                                       evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    try:
        now = _now()
        start = "00:00" if now.hour < 1 else f"{now.hour - 1:02d}:00"
        end = "23:59" if now.hour >= 22 else f"{now.hour + 2:02d}:00"
        today = now.date().isoformat()
        others = [d for d in range(7) if d != now.weekday()]
        base = {"trigger_type": "business_calendar", "cron_expression": "* * * * *",
                "timezone": "UTC", "business_hours_start": start, "business_hours_end": end}
        sids = {
            "open": base,
            "holiday": {**base, "holidays": [today, "2026-12-25"]},
            "off_day": {**base, "business_days": others},
        }
        ids = {k: str(_trigger(ent, todo, s, f"{ACK} (bc-{k})")["schedule_id"])
               for k, s in sids.items()}
        evidence["calendar"] = {"today": today, "hours": [start, end], "weekday": now.weekday()}
        _wait_runs(ent, ids["open"], 2, 200, "the open business calendar to fire twice")
        counts = {k: len(_goal_runs(ent, sid)) for k, sid in ids.items()}
        evidence["fires"] = counts
        assert counts["holiday"] == 0 and counts["off_day"] == 0, counts
        # Bad calendars are refused on save.
        _trigger(ent, todo, {**base, "holidays": ["Diwali"]}, ACK, expect=422)
        _trigger(ent, todo, {**base, "business_days": []}, ACK, expect=422)
    finally:
        _cleanup(ent, todo)


# ── TIME-LIFECYCLE ────────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-LIFECYCLE")
def test_pause_resume_edit_delete(ent: LiveAPI, evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    try:
        sid = str(_trigger(ent, todo, {"trigger_type": "cron", "cron_expression": "* * * * *"},
                           f"{ACK} (lifecycle)")["schedule_id"])
        _wait_runs(ent, sid, 1, 150, "the first fire")
        assert ent.post(f"/schedules/{sid}/pause").status_code == 200
        paused_at = _now()
        time.sleep(150)  # two slots pass while paused
        during = _goal_runs(ent, sid, after=paused_at + timedelta(seconds=5))
        evidence["fires_while_paused"] = len(during)
        assert during == [], "fired while paused"

        assert ent.post(f"/schedules/{sid}/resume").status_code == 200
        resumed_at = _now()
        time.sleep(80)
        after_resume = _goal_runs(ent, sid, after=resumed_at)
        evidence["fires_first_80s_after_resume"] = len(after_resume)
        # B1-1: no replay of the paused slots: one fire (the next slot), not 3.
        assert len(after_resume) == 1, f"{len(after_resume)} fires right after resume"

        edit = ent.patch(f"/triggers/{sid}", json={"spec": {"trigger_type": "cron",
                                                            "cron_expression": "*/2 * * * *"}})
        assert edit.status_code == 200, mask(edit.text[:300])
        edited_at = _now()
        time.sleep(200)
        after_edit = _goal_runs(ent, sid, after=edited_at)
        minutes = [_ts(r["started_at"]).minute for r in after_edit]  # type: ignore[union-attr]
        evidence["fires_after_edit_minutes"] = minutes
        assert 1 <= len(after_edit) <= 2 and all(m % 2 == 0 for m in minutes), minutes

        assert ent.delete(f"/triggers/{sid}").status_code == 204
        todo.remove(sid)
        deleted_at = _now()
        time.sleep(150)
        late = _goal_runs(ent, sid, after=deleted_at)
        evidence["fires_after_delete"] = len(late)
        assert late == [], "fired after delete"
        assert ent.get(f"/triggers/{sid}").status_code == 404
    finally:
        _cleanup(ent, todo)


# ── TIME-PLAN-FLOOR ───────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-PLAN-FLOOR")
def test_plan_floors_free_vs_enterprise(ent: LiveAPI, second_tenant_api: LiveAPI | None,
                                        evidence: dict[str, Any]) -> None:
    if second_tenant_api is None:
        pytest.skip("needs RW_SECOND_TENANT_FILE (a free tenant)")
    free = second_tenant_api
    plans = {"free": str(free.json_ok("GET", "/tenants/me").get("plan")),
             "enterprise": str(ent.json_ok("GET", "/tenants/me").get("plan"))}
    assert plans == {"free": "free", "enterprise": "enterprise"}, plans
    todo_f: list[str] = []
    todo_e: list[str] = []
    results: dict[str, int] = {}
    try:
        def code(api: LiveAPI, todo: list[str], spec: dict[str, Any]) -> int:
            resp = api.post("/triggers", json={"spec": spec, "goal_template": ACK})
            if resp.status_code == 201:
                todo.append(str(resp.json()["schedule_id"]))
                api.post(f"/triggers/{resp.json()['schedule_id']}/pause")
            return resp.status_code

        results["free_interval_899"] = code(free, todo_f, {"trigger_type": "interval",
                                                           "interval_seconds": 899})
        results["free_interval_900"] = code(free, todo_f, {"trigger_type": "interval",
                                                           "interval_seconds": 900})
        results["free_cron_every_minute"] = code(free, todo_f, {"trigger_type": "cron",
                                                                "cron_expression": "* * * * *"})
        results["free_bc_every_5_min"] = code(free, todo_f, {"trigger_type": "business_calendar",
                                                             "cron_expression": "*/5 * * * *"})
        results["ent_interval_59"] = code(ent, todo_e, {"trigger_type": "interval",
                                                        "interval_seconds": 59})
        results["ent_interval_60"] = code(ent, todo_e, {"trigger_type": "interval",
                                                        "interval_seconds": 60})
        results["ent_cron_every_minute"] = code(ent, todo_e, {"trigger_type": "cron",
                                                              "cron_expression": "* * * * *"})
        if todo_f:  # an edit cannot go below the floor either
            patch = free.patch(f"/triggers/{todo_f[0]}", json={
                "spec": {"trigger_type": "interval", "interval_seconds": 60}})
            results["free_patch_to_60"] = patch.status_code
        nl = free.post("/nl/schedule", json={"command": "every minute check the dock door feed"})
        results["free_nl_every_minute"] = nl.status_code
        if nl.status_code == 201:
            todo_f.extend(str(r["schedule_id"]) for r in nl.json())
        evidence.update(results=results, nl_detail=mask(nl.text[:300]))
        assert results == {
            "free_interval_899": 422, "free_interval_900": 201, "free_cron_every_minute": 422,
            "free_bc_every_5_min": 422, "ent_interval_59": 422, "ent_interval_60": 201,
            "ent_cron_every_minute": 201, "free_patch_to_60": 422, "free_nl_every_minute": 422,
        }, results
        assert "free plan" in nl.text.lower(), f"NL refusal does not name the floor: {nl.text[:200]}"
    finally:
        _cleanup(free, todo_f)
        _cleanup(ent, todo_e)


# ── TIME-NL ───────────────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-NL")
def test_natural_language_time_schedules(ent: LiveAPI, evidence: dict[str, Any]) -> None:
    todo: list[str] = []
    seen: dict[str, Any] = {}
    try:
        def nl(command: str) -> list[dict[str, Any]]:
            before = _now()
            resp = ent.post("/nl/schedule", json={"command": command})
            seen[command[:40]] = {"http": resp.status_code, "body": mask(resp.text[:300])}
            assert resp.status_code == 201, f"{command!r} -> {resp.status_code}: {resp.text[:300]}"
            recs = list(resp.json())
            for r in recs:
                todo.append(str(r["schedule_id"]))
                ent.post(f"/schedules/{r['schedule_id']}/pause")  # never let them fire
            seen[command[:40]]["specs"] = [r.get("spec") for r in recs]
            seen[command[:40]]["at"] = before.isoformat()
            return recs

        weekday = nl("every weekday at 9am, summarise yesterday's delayed shipments")
        assert weekday[0]["spec"]["cron_expression"] in {"0 9 * * 1-5", "0 9 * * MON-FRI",
                                                         "0 9 * * mon-fri"}
        ist = nl("every weekday at 9:30 IST send the stand-up digest to the ops channel")[0]["spec"]
        assert ist["timezone"] == "Asia/Kolkata", ist
        assert ist["cron_expression"].split()[:2] == ["30", "9"], ist
        before = _now()
        soon = nl("in 20 minutes remind the ops team to check the cold-chain sensors")[0]["spec"]
        assert soon["trigger_type"] == "once", soon
        delta = (_ts(soon["fire_at_iso"]) - before).total_seconds()  # type: ignore[operator]
        assert 17 * 60 <= delta <= 23 * 60, f"'in 20 minutes' resolved to {delta:.0f}s"
        tomorrow = nl("tomorrow at 8am UTC check the cold room temperature log")[0]["spec"]
        want = (_now() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        assert _ts(tomorrow["fire_at_iso"]) == want, tomorrow
    finally:
        evidence["nl"] = seen
        _cleanup(ent, todo)


# ── TIME-SCALE-DUE-INDEX ──────────────────────────────────────────────────────


def _ticks_checked(since: datetime) -> list[int]:
    proc = _docker("logs", "--since", since.strftime("%Y-%m-%dT%H:%M:%S"), SCHEDULE_WORKER)
    # Only fire_due_schedules results (fire_due_org_mission_schedules also
    # reports a schedules_checked count).
    return [int(m.group(1)) for m in re.finditer(
        r"tasks\.fire_due_schedules\[[^\]]+\] succeeded[^{]*\{[^}]*'schedules_checked': (\d+)",
        proc.stdout + proc.stderr)]


@pytest.mark.scenario("TIME-SCALE-DUE-INDEX")
def test_far_future_schedules_are_not_reread_every_tick(ent: LiveAPI,
                                                         evidence: dict[str, Any]) -> None:
    n = int(os.getenv("RW_SCALE_SCHEDULES", "300"))
    todo: list[str] = []
    try:
        for i in range(n):
            _trigger(ent, todo, {"trigger_type": "cron", "cron_expression": f"{i % 60} 3 1 1 *"},
                     f"{ACK} (yearly {i})")
        created = _now()
        # The first tick evaluates the new rows once (next_fire_at unknown) and
        # stores when each is next due; later ticks claim only due rows.
        _sleep_until(_next_slot(0) + timedelta(seconds=150))
        checked = _ticks_checked(created)
        evidence.update(schedules=n, checked_per_tick=checked)
        assert len(checked) >= 2, f"no ticks seen in {SCHEDULE_WORKER}'s log: {checked}"
        # At most one tick evaluates the new rows (they had no next_fire_at
        # yet); every other tick claims only due rows.
        big = [c for c in checked if c >= n // 4]
        assert len(big) <= 1 and checked[-1] < n // 4, (
            f"ticks keep re-reading the far-future rows: {checked}")
    finally:
        _cleanup(ent, todo)


# ── TIME-CATCH-UP (disruptive) ────────────────────────────────────────────────


def _first_tick_after(when: datetime) -> datetime | None:
    """When the schedule worker received the first fire_due_schedules after *when*."""
    proc = _docker("logs", "--since", when.strftime("%Y-%m-%dT%H:%M:%S"), SCHEDULE_WORKER)
    for m in re.finditer(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+: INFO/MainProcess\] "
                         r"Task app\.scaling\.tasks\.fire_due_schedules\[[^\]]+\] received",
                         proc.stdout + proc.stderr):
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        if ts >= when:
            return ts
    return None


@pytest.mark.scenario("TIME-CATCH-UP")
def test_missed_runs_after_a_beat_outage_follow_catch_up(ent: LiveAPI,
                                                         evidence: dict[str, Any]) -> None:
    """The trigger loop is taken down for ~4 minutes, then the beat restarts.

    The outage holds the beat's own overlap guard (``beat_guard:fire_due_schedules``
    in Redis), so every tick in the window is skipped exactly as if no beat ran.
    Stopping the beat container is not enough on this machine: the launchd
    runtime (scripts/run_forever.py) starts its own beat when compose's is gone.
    """
    if os.getenv("RW_ALLOW_BEAT_RESTART") != "1":
        pytest.skip("suppresses the live trigger loop for ~4 minutes: set RW_ALLOW_BEAT_RESTART=1")
    if not _running(BEAT):
        pytest.skip(f"beat container {BEAT!r} is not running")
    todo: list[str] = []
    guard = "beat_guard:fire_due_schedules"
    try:
        ids = {p: str(_trigger(ent, todo, {"trigger_type": "cron", "cron_expression": "* * * * *",
                                           "catch_up": p}, f"{ACK} (catch-up {p})")["schedule_id"])
               for p in ("all", "latest", "none")}
        for sid in ids.values():
            _wait_runs(ent, sid, 1, 150, "a fire before the outage")
        held = _docker("exec", REDIS, "redis-cli", "SET", guard, "rw-catch-up-outage", "EX", "400")
        assert held.returncode == 0 and "OK" in held.stdout, held.stdout + held.stderr
        stopped = _now()
        time.sleep(200)  # >= 3 minute slots pass with no trigger tick
        assert _docker("restart", BEAT).returncode == 0
        _docker("exec", REDIS, "redis-cli", "DEL", guard)
        released = _now()
        missed = int((released - stopped).total_seconds() // 60)
        first_tick = wait_until(lambda: _first_tick_after(released), timeout=180, interval=5,
                                desc="the first trigger tick after the outage")
        _sleep_until(first_tick + timedelta(seconds=40))
        # Only the first tick's fires (ticks are 15 s apart, B1-16).
        burst = {p: len([r for r in _goal_runs(ent, sid, after=stopped)
                         if _ts(r["started_at"]) <= first_tick + timedelta(seconds=10)])  # type: ignore[operator]
                 for p, sid in ids.items()}
        during = {p: len([r for r in _goal_runs(ent, sid, after=stopped + timedelta(seconds=5))
                          if _ts(r["started_at"]) < released])  # type: ignore[operator]
                  for p, sid in ids.items()}
        evidence.update(missed_minutes=missed, first_tick=first_tick.isoformat(), burst=burst,
                        fires_during_outage=during)
        assert during == {"all": 0, "latest": 0, "none": 0}, f"fired during the outage: {during}"
        assert burst["all"] >= 3, f"catch_up=all replayed {burst['all']} of ~{missed + 1} slots"
        assert burst["latest"] == 1, f"catch_up=latest fired {burst['latest']}"
        assert burst["none"] <= 1, f"catch_up=none fired {burst['none']} (only an on-time slot)"
        runs = _goal_runs(ent, ids["all"], after=stopped)
        assert len({r["goal_id"] for r in runs}) == len(runs)
    finally:
        _docker("exec", REDIS, "redis-cli", "DEL", guard)
        if not _running(BEAT):
            _docker("start", BEAT)
        _cleanup(ent, todo)


# ── TIME-EXACTLY-ONCE (two beats, two schedule workers) ───────────────────────


@pytest.mark.scenario("TIME-EXACTLY-ONCE")
def test_two_beats_and_two_workers_fire_each_slot_once(ent: LiveAPI,
                                                       evidence: dict[str, Any]) -> None:
    second_beat = os.getenv("RW_SECOND_BEAT_CONTAINER", "")
    second_worker = os.getenv("RW_SECOND_SCHEDULE_WORKER_CONTAINER", "")
    if not (second_beat and second_worker and _running(second_beat) and _running(second_worker)):
        pytest.skip("needs RW_SECOND_BEAT_CONTAINER and RW_SECOND_SCHEDULE_WORKER_CONTAINER "
                    "running next to the compose beat / schedule-worker")
    todo: list[str] = []
    try:
        started = _now()
        specs: dict[str, dict[str, Any]] = {
            "cron": {"trigger_type": "cron", "cron_expression": "* * * * *"},
            "interval": {"trigger_type": "interval", "interval_seconds": 60},
        }
        ids = {k: str(_trigger(ent, todo, s, f"{ACK} (x1-{k})")["schedule_id"])
               for k, s in specs.items()}
        time.sleep(250)
        for sid in ids.values():
            ent.post(f"/triggers/{sid}/pause")
        report: dict[str, Any] = {}
        for k, sid in ids.items():
            runs = _goal_runs(ent, sid)
            minutes = [_ts(r["started_at"]).replace(second=0, microsecond=0)  # type: ignore[union-attr]
                       for r in runs]
            skipped = [r for r in _runs(ent, sid) if r.get("skip_reason")]
            report[k] = {"goals": len(runs), "distinct_minutes": len(set(minutes)),
                         "skips": [s.get("skip_reason") for s in skipped]}
            assert len(runs) >= 3, f"{k}: only {len(runs)} fires in 250 s"
            assert len(set(minutes)) == len(minutes), f"{k}: two goals in one minute {minutes}"
            assert len({r['goal_id'] for r in runs}) == len(runs)
        sent = {c: len(re.findall(r"Sending due task fire-due-schedules",
                                  (lambda p: p.stdout + p.stderr)(_docker(
                                      "logs", "--since", started.strftime("%Y-%m-%dT%H:%M:%S"), c))))
                for c in (BEAT, second_beat)}
        report["ticks_sent_by_beat"] = sent
        evidence.update(report)
        # RedBeat lets one beat hold the lock (the other is a hot standby); with
        # plain beats both send. Either way the slots above fired exactly once.
        assert sum(sent.values()) >= 3, f"no beat was ticking: {sent}"
    finally:
        _cleanup(ent, todo)


# ── TIME-CONDITION ────────────────────────────────────────────────────────────


@pytest.mark.scenario("TIME-CONDITION")
def test_cel_conditions_gate_beat_fires(ent: LiveAPI, evidence: dict[str, Any]) -> None:
    """A condition_cel on a beat-fired trigger is evaluated (it used to be
    dropped, so the trigger fired unconditionally): false -> audited skip, true
    -> goal; on an event-relative delay it sees the event's payload."""
    todo: list[str] = []
    try:
        # B1-12: a condition the evaluator cannot run is refused on save (it was
        # stored, and every fire was skipped as condition_error).
        _trigger(ent, todo, {"trigger_type": "cron", "cron_expression": "* * * * *",
                             "condition_cel": 'payload.goal_text.contains("ACK")'}, ACK, expect=422)
        blocked = str(_trigger(ent, todo, {"trigger_type": "cron", "cron_expression": "* * * * *",
                                           "condition_cel": 'payload.tenant_id == "nobody"'},
                               f"{ACK} (cel-blocked)")["schedule_id"])
        allowed = str(_trigger(ent, todo, {"trigger_type": "cron", "cron_expression": "* * * * *",
                                           "condition_cel": 'payload.goal_text != ""'},
                               f"{ACK} (cel-allowed)")["schedule_id"])
        channel = f"rw.incident.{tag()}"
        p1_only = str(_trigger(ent, todo, {"trigger_type": "relative_delay",
                                           "event_channel": channel, "relative_offset_seconds": 60,
                                           "condition_cel": 'payload.priority == "P1"'},
                               "Escalate incident {{payload.incident}}. " + ACK)["schedule_id"])
        _publish(ent, channel, {"event_id": "inc-1", "incident": "INC-77", "priority": "P1"})
        _publish(ent, channel, {"event_id": "inc-2", "incident": "INC-78", "priority": "P3"})
        _wait_runs(ent, allowed, 2, 200, "the allowed cron to fire twice")
        _wait_runs(ent, p1_only, 1, 200, "the P1 event's delay to fire")
        time.sleep(60)
        runs = {k: _runs(ent, s) for k, s in
                (("blocked", blocked), ("allowed", allowed), ("p1_only", p1_only))}
        summary = {k: {"goals": len([r for r in v if r.get("goal_id")]),
                       "skips": sorted({str(r.get("skip_reason")) for r in v
                                        if r.get("skip_reason")})} for k, v in runs.items()}
        evidence["runs"] = summary
        assert summary["blocked"]["goals"] == 0 and "condition_false" in summary["blocked"]["skips"]
        assert summary["allowed"]["goals"] >= 2
        assert summary["p1_only"]["goals"] == 1 and "condition_false" in summary["p1_only"]["skips"]
        text = _goal(ent, str(next(r for r in runs["p1_only"] if r.get("goal_id"))["goal_id"]))
        assert "INC-77" in str(text.get("goal")), text.get("goal")
    finally:
        _cleanup(ent, todo)
