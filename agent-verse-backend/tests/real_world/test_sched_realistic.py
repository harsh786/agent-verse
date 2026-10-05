"""SCHED-REALISTIC: schedules a team would actually set up.

* SCHED-CRUD: a weekday-9am cron within the plan floor; next-fire time matches the
  cron (croniter ground truth); pause / resume / edit (cron changes, next fire
  moves) / delete (gone afterwards).
* SCHED-PLAN-FLOOR: an interval at the plan floor is accepted; one second below it
  and an every-minute cron (when the floor is above a minute) are 422.
* SCHED-NL: "every weekday at 9am" becomes the weekday-9am cron.
* SCHED-FIRES-GOAL / SCHED-FIRES-WORKFLOW: a schedule really fires a goal / a
  workflow run. Uses RW_ENTERPRISE_API_KEY's tenant when given (short floors);
  otherwise the default tenant's floor, and SKIPS when that floor is longer than
  RW_SCHEDULE_MAX_WAIT seconds.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.real_world import wf_complex as wfc
from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, mask, schedule_history_runs, tag, wait_until

MAX_WAIT = float(os.getenv("RW_SCHEDULE_MAX_WAIT", "960"))
WEEKDAY_9AM = {"0 9 * * 1-5", "0 9 * * mon-fri", "00 09 * * 1-5", "0 9 * * 1,2,3,4,5"}


def _floor(api: LiveAPI) -> tuple[str, int]:
    plan = str(api.json_ok("GET", "/tenants/me").get("plan") or "free")
    floors = api.json_ok("GET", "/schedules/plan-floors").get("plan_min_interval_seconds") or {}
    return plan, int(floors.get(plan) or floors.get("free") or 60)


def _cron_of(rec: dict[str, Any]) -> str:
    spec = rec.get("spec") or {}
    return str(spec.get("cron_expression") or rec.get("cron_expr") or "").strip().lower()


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _next_fire(api: LiveAPI, sid: str, timeout: float = 90) -> datetime | None:
    try:
        rec = wait_until(lambda: api.json_ok("GET", f"/triggers/{sid}"), timeout=timeout,
                         interval=5, desc="next_fire_at", done=lambda r: bool(
                             r.get("next_fire_at")))
    except AssertionError:
        return None
    return _parse_ts(rec.get("next_fire_at"))


def _expected_next(cron: str) -> datetime:
    from croniter import croniter

    return croniter(cron, datetime.now(UTC)).get_next(datetime)


@pytest.mark.scenario("SCHED-CRUD")
def test_schedule_lifecycle(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cron = "0 9 * * 1-5"
    created = api.post("/schedules", json={
        "trigger_type": "cron", "cron_expr": cron, "name": f"rw-weekday-digest-{tag()}",
        "goal_template": "Summarise yesterday's late shipments for the ops stand-up."})
    assert created.status_code == 201, f"create -> {created.status_code}: " \
        f"{mask(created.text[:300])}"
    rec = created.json()
    sid = str(rec.get("schedule_id"))
    cleanup("DELETE", f"/schedules/{sid}")
    evidence["schedule_id"] = sid
    assert _cron_of(rec) == cron, rec
    soft: list[str] = []
    nxt = _next_fire(api, sid)
    want = _expected_next(cron)
    evidence["next_fire"] = {"reported": nxt and nxt.isoformat(), "expected": want.isoformat()}
    if nxt is None:
        soft.append("next_fire_at is never reported for an active cron schedule")
    elif abs((nxt - want).total_seconds()) > 90:
        soft.append(f"next_fire_at {nxt.isoformat()} does not match the cron ({want.isoformat()})")

    p = api.post(f"/schedules/{sid}/pause")
    assert p.status_code == 200 and p.json().get("paused") is True, mask(p.text[:200])
    assert api.json_ok("GET", f"/triggers/{sid}").get("paused") is True, "pause not persisted"
    r = api.post(f"/schedules/{sid}/resume")
    assert r.status_code == 200 and r.json().get("paused") is False, mask(r.text[:200])
    assert api.json_ok("GET", f"/triggers/{sid}").get("paused") is False, "resume not persisted"

    new_cron = "30 10 * * 1-5"
    edit = api.patch(f"/triggers/{sid}", json={"spec": {"trigger_type": "cron",
                                                        "cron_expression": new_cron}})
    evidence["edit_http"] = edit.status_code
    assert edit.status_code == 200, f"edit -> {edit.status_code}: {mask(edit.text[:300])}"
    after = api.json_ok("GET", f"/triggers/{sid}")
    assert _cron_of(after) == new_cron, f"edit not persisted: {_cron_of(after)!r}"
    nxt2 = _next_fire(api, sid)
    want2 = _expected_next(new_cron)
    evidence["next_fire_after_edit"] = {"reported": nxt2 and nxt2.isoformat(),
                                        "expected": want2.isoformat()}
    if nxt2 is not None and abs((nxt2 - want2).total_seconds()) > 90:
        soft.append(f"after the edit next_fire_at {nxt2.isoformat()} still follows the old cron")
    listed = {str(s.get("schedule_id")) for s in api.json_ok("GET", "/schedules")}
    assert sid in listed, "schedule missing from GET /schedules"
    d = api.delete(f"/schedules/{sid}")
    assert d.status_code == 204, f"delete -> {d.status_code}"
    assert api.get(f"/schedules/{sid}").status_code == 404, "deleted schedule still readable"
    assert sid not in {str(s.get("schedule_id")) for s in api.json_ok("GET", "/schedules")}
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("SCHED-PLAN-FLOOR")
def test_schedule_plan_floor(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    plan, floor = _floor(api)
    evidence.update(plan=plan, floor_s=floor)
    ok = api.post("/schedules", json={"trigger_type": "interval", "interval_seconds": floor,
                                      "name": f"rw-floor-ok-{tag()}",
                                      "goal_template": "Check the dock-door sensor feed."})
    evidence["at_floor_http"] = ok.status_code
    assert ok.status_code == 201, f"interval at the plan floor refused: {mask(ok.text[:300])}"
    cleanup("DELETE", f"/schedules/{ok.json().get('schedule_id')}")
    api.post(f"/schedules/{ok.json().get('schedule_id')}/pause")  # never let it fire
    below = api.post("/schedules", json={"trigger_type": "interval",
                                         "interval_seconds": max(1, floor - 1),
                                         "goal_template": "too often"})
    evidence["below_floor_http"] = below.status_code
    if below.status_code == 201:
        cleanup("DELETE", f"/schedules/{below.json().get('schedule_id')}")
    assert below.status_code == 422, f"interval below the floor answered {below.status_code}"
    if floor > 60:
        minute = api.post("/schedules", json={"trigger_type": "cron", "cron_expr": "* * * * *",
                                              "goal_template": "every minute"})
        evidence["every_minute_cron_http"] = minute.status_code
        if minute.status_code == 201:
            cleanup("DELETE", f"/schedules/{minute.json().get('schedule_id')}")
        assert minute.status_code == 422, f"every-minute cron on plan {plan} answered " \
            f"{minute.status_code}"


@pytest.mark.scenario("SCHED-NL")
def test_schedule_from_natural_language(api: LiveAPI, cleanup: Any,
                                        evidence: dict[str, Any]) -> None:
    resp = api.post("/nl/schedule", json={
        "command": "every weekday at 9am, summarise yesterday's delayed shipments"})
    evidence["http"] = resp.status_code
    assert resp.status_code == 201, f"/nl/schedule -> {resp.status_code}: {mask(resp.text[:300])}"
    recs = resp.json()
    for r in recs:
        cleanup("DELETE", f"/schedules/{r.get('schedule_id')}")
    evidence["parsed"] = [{"type": (r.get("spec") or {}).get("trigger_type"),
                           "cron": _cron_of(r)} for r in recs]
    assert len(recs) == 1, f"one sentence produced {len(recs)} schedules"
    assert _cron_of(recs[0]) in WEEKDAY_9AM, (
        f"'every weekday at 9am' parsed as {_cron_of(recs[0])!r}, expected 0 9 * * 1-5"
    )


def _client_for_firing(api: LiveAPI, enterprise_api: LiveAPI | None) -> tuple[LiveAPI, int, str]:
    client = enterprise_api or api
    plan, floor = _floor(client)
    if floor > MAX_WAIT:
        pytest.skip(f"plan {plan!r} floor is {floor}s > RW_SCHEDULE_MAX_WAIT={MAX_WAIT:.0f}s; "
                    "set RW_ENTERPRISE_API_KEY (short floors) or raise RW_SCHEDULE_MAX_WAIT")
    return client, floor, plan


@pytest.mark.scenario("SCHED-FIRES-GOAL")
def test_schedule_fires_goal(api: LiveAPI, enterprise_api: LiveAPI | None,
                             evidence: dict[str, Any]) -> None:
    client, floor, plan = _client_for_firing(api, enterprise_api)
    interval = max(floor, 60)
    evidence.update(plan=plan, interval_s=interval)
    created = client.post("/schedules", json={
        "trigger_type": "interval", "interval_seconds": interval,
        "name": f"rw-fire-goal-{tag()}",
        "goal_template": "Reply with exactly the word ACK. Do not use any tools."})
    assert created.status_code == 201, mask(created.text[:300])
    sid = str(created.json().get("schedule_id"))
    evidence["schedule_id"] = sid
    goal_ids: list[str] = []
    try:
        hist = wait_until(
            lambda: client.json_ok("GET", f"/schedules/{sid}/history"),
            timeout=interval + 180, interval=10, desc="the schedule to fire a goal",
            done=lambda h: any(r.get("goal_id") for r in schedule_history_runs(h)))
        goal_ids = [str(r["goal_id"]) for r in schedule_history_runs(hist) if r.get("goal_id")]
        evidence["fired_goal_ids"] = goal_ids
        goal = client.json_ok("GET", f"/goals/{goal_ids[0]}")
        evidence["goal_status"] = goal.get("status")
        assert "ACK" in str(goal.get("goal", "")), "the fired goal is not the schedule's template"
    finally:
        client.delete(f"/schedules/{sid}")
        for gid in goal_ids:
            client.post(f"/goals/{gid}/cancel")


@pytest.mark.scenario("SCHED-FIRES-WORKFLOW")
def test_schedule_fires_workflow(api: LiveAPI, enterprise_api: LiveAPI | None,
                                 evidence: dict[str, Any]) -> None:
    client, floor, plan = _client_for_firing(api, enterprise_api)
    minutes = max(1, -(-floor // 60))
    cron = "* * * * *" if minutes == 1 else f"*/{minutes} * * * *"
    evidence.update(plan=plan, cron=cron)
    todo: list[tuple[str, str]] = []
    wf_id = wfx.import_yaml(client, lambda m, p: todo.append((m, p)),
                            wfc.scheduled_ping_yaml(f"rw-sched-ping-{tag()}", cron))
    evidence["workflow_id"] = wf_id
    try:
        pub = client.post(f"{wfx.V1}/workflows/{wf_id}/publish")
        evidence["publish_http"] = pub.status_code
        assert pub.status_code == 200, f"publish -> {pub.status_code}: {mask(pub.text[:300])}"

        def runs() -> list[dict[str, Any]]:
            body = client.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": wf_id})
            return list(body.get("items", []) if isinstance(body, dict) else body)

        found = wait_until(runs, timeout=minutes * 60 + 180, interval=10,
                           desc=f"cron {cron!r} to fire a run")
        run_id = str(found[0].get("run_id"))
        evidence["run_id"] = run_id
        client.post(f"{wfx.V1}/workflows/{wf_id}/unpublish")
        done = wfx.wait_status(client, run_id, {"complete"}, 180)
        assert done.get("status") == "complete", mask(done.get("error"))
        debug = client.get(f"{wfx.V1}/runs/{run_id}/debug")
        trig = (debug.json().get("run") or {}).get("trigger_type") if debug.status_code == 200 \
            else None
        evidence["trigger_type"] = trig
        assert trig in (None, "schedule"), f"run was started by {trig!r}, not the schedule"
        out = wfx.output_of(wfx.get_steps(client, run_id), "heartbeat")
        assert out.get("alive") is True, out
    finally:
        client.post(f"{wfx.V1}/workflows/{wf_id}/unpublish")
        for method, path in reversed(todo):
            client.request(method, path)
