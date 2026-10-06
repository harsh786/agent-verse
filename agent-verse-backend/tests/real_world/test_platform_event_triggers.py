"""PLATFORM-*: platform-event triggers on the live stack (B7).

Six trigger types fire on events the platform itself produces while it runs
goals: goal_completed / goal_failed / goal_score_below (a goal's lifecycle),
hitl_approved / hitl_rejected (an approval gate of a goal or a workflow run) and
memory_created (a goal's learning). Every scenario drives real goals, real
approval decisions and real learnings through the public API and asserts what an
operator would check on ``GET /triggers/{id}/events`` (the trigger's audit
trail: one row per fire, one row per suppressed fire with its reason):

* PLATFORM-GOAL-CHAIN: goal A (agent src) completes -> trigger T1 (watches src,
  runs agent dst) fires exactly once -> goal B; B completes -> T2 (watches dst,
  runs dst) fires once -> goal C; C's completion is T2's own goal: audited
  ``self_trigger``, no goal. T1 never fires for B / C (source-agent filter). The
  other tenant's unfiltered goal_completed trigger never fires on these goals.
* PLATFORM-SELF-TRIGGER-DEPTH: ``allow_self_trigger`` opts a trigger into firing
  on its own goals; the chain stops at MAX_CHAIN_DEPTH (10 goals) and the
  refused 11th firing is audited ``chain_depth_exceeded``.
* PLATFORM-GOAL-FAILED: goal_failed fires once for a goal the worker fails, and
  for goals failed by the stuck-goal detector, the stale-runner watchdog and the
  expired-approval sweep (the beat paths, on rows placed in those states).
* PLATFORM-SCORE-BELOW: create needs a 0..1 threshold (422 otherwise); a scored
  goal fires each trigger iff the watched score (overall or one dimension) is
  below its threshold; a completed goal scores task_completion 1.0.
* PLATFORM-HITL-GOAL: a supervised goal's approval gate: approve -> the
  hitl_approved trigger (queue ``agent:<id>``) fires once per decision; the same
  decision relayed again on the bus fires nothing; reject -> hitl_rejected once.
* PLATFORM-HITL-WORKFLOW: the same for a workflow run's approval step, incl. a
  repeated decide call and a relayed duplicate.
* PLATFORM-MEMORY: a goal's learning fires memory_created once; the learning of
  the trigger's own goal is audited ``self_trigger`` (no loop).
* PLATFORM-QUOTA-RATE: the free plan's trigger quota (403) and a trigger's
  hourly cap: the second firing is audited ``rate_limit`` and dead-lettered.
* PLATFORM-MULTI-REPLICA (``RW_SECOND_BACKEND_CONTAINER`` running): with two API
  replicas consuming the bus, each goal completion fires exactly once.

Uses the session tenant (``api``) and ``RW_SECOND_TENANT_*`` (free plan) for
isolation and quota. Rows placed in the stuck / stale-runner / expired-approval
states are written with ``docker exec`` psql (``RW_PG_CONTAINER``) and deleted.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import uuid
from typing import Any

import pytest

from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, mask, tag, wait_until

ACK = " Reply with exactly the word ACK. Do not use any tools."
REDIS = os.getenv("RW_REDIS_CONTAINER", "agentverse-backend-redis-1")
PG = os.getenv("RW_PG_CONTAINER", "agentverse-backend-postgres-1")
PG_USER = os.getenv("RW_PG_SUPERUSER", "agentverse")
SECOND_BACKEND = os.getenv("RW_SECOND_BACKEND_CONTAINER", "")
GOAL_TIMEOUT = float(os.getenv("RW_GOAL_TIMEOUT", "480"))
MAX_CHAIN_DEPTH = 10
TERMINAL = {"complete", "failed", "cancelled", "rejected"}
HIGH_RISK_GOAL = (
    "Demo list (in-memory sample data, no external systems or tools needed): "
    "rec-{t}-1 env=staging last_used=2025-01-03; rec-{t}-2 env=production last_used=2026-09-28; "
    "rec-{t}-3 env=staging last_used=2025-02-11. Delete the stale staging records "
    "(env=staging and last_used before 2026) from the demo list and report which record IDs "
    "were removed and which remain."
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _agent(api: LiveAPI, cleanup: Any, prefix: str, **fields: Any) -> str:
    body = {"name": f"{prefix}-{tag()}", "max_iterations": 4, "timeout_seconds": 300, **fields}
    created = api.json_ok("POST", "/agents", json=body)
    agent_id = str(created.get("agent_id") or created.get("id"))
    cleanup("DELETE", f"/agents/{agent_id}")
    return agent_id


def _trigger(api: LiveAPI, cleanup: Any, spec: dict[str, Any], template: str,
             agent_id: str = "") -> str:
    body: dict[str, Any] = {"spec": spec, "goal_template": template}
    if agent_id:
        body["agent_id"] = agent_id
    resp = api.post("/triggers", json=body)
    assert resp.status_code == 201, f"POST /triggers -> {resp.status_code}: {mask(resp.text[:300])}"
    sid = str(resp.json()["schedule_id"])
    cleanup("DELETE", f"/triggers/{sid}")
    return sid


def _submit(api: LiveAPI, cleanup: Any, goal: str, **fields: Any) -> str:
    resp = api.post("/goals", json={"goal": goal, **fields})
    assert resp.status_code in (200, 201, 202), f"POST /goals -> {resp.status_code}: " \
        f"{mask(resp.text[:300])}"
    gid = str(resp.json().get("goal_id") or resp.json().get("id"))
    cleanup("POST", f"/goals/{gid}/cancel")
    return gid


def _goal(api: LiveAPI, goal_id: str) -> dict[str, Any]:
    resp = api.get(f"/goals/{goal_id}")
    return dict(resp.json()) if resp.status_code == 200 else {"status": f"http {resp.status_code}"}


def _wait_goal(api: LiveAPI, goal_id: str, timeout: float = GOAL_TIMEOUT) -> dict[str, Any]:
    return dict(wait_until(lambda: _goal(api, goal_id), timeout=timeout, interval=4,
                           desc=f"goal {goal_id} to finish",
                           done=lambda g: g.get("status") in TERMINAL))


def _events(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    resp = api.get(f"/triggers/{sid}/events", params={"limit": 200})
    return list(resp.json()) if resp.status_code == 200 else []


def _fired(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    return [e for e in _events(api, sid) if e.get("goal_id") and e.get("goal_created")]


def _skips(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    return [e for e in _events(api, sid) if e.get("skip_reason")]


def _src(e: dict[str, Any], key: str = "goal_id") -> str:
    return str((e.get("payload") or {}).get(key) or "")


def _wait_fired(api: LiveAPI, sid: str, n: int, *, settle: float = 15.0,
                timeout: float = GOAL_TIMEOUT) -> list[dict[str, Any]]:
    wait_until(lambda: _fired(api, sid), timeout=timeout, interval=4,
               desc=f"{n} firing(s) of trigger {sid}", done=lambda f: len(f) >= n)
    time.sleep(settle)  # a duplicate would show up now
    return _fired(api, sid)


def _wait_skip(api: LiveAPI, sid: str, reason: str, *, timeout: float = GOAL_TIMEOUT
               ) -> list[dict[str, Any]]:
    return list(wait_until(
        lambda: [e for e in _skips(api, sid) if e.get("skip_reason") == reason],
        timeout=timeout, interval=4, desc=f"a {reason} audit row on trigger {sid}"))


def _cancel_fired(api: LiveAPI, cleanup: Any, sid: str) -> None:
    for e in _fired(api, sid):
        cleanup("POST", f"/goals/{e['goal_id']}/cancel")


def _psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG, "psql", "-U", PG_USER, "-d", "agentverse", "-v",
         "ON_ERROR_STOP=1", "-At", "-c", sql],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"psql failed: {out.stderr[:400]}"
    return out.stdout.strip()


def _redis(*args: str) -> str:
    return subprocess.run(["docker", "exec", REDIS, "redis-cli", *args], capture_output=True,
                          text=True, timeout=30, check=True).stdout


def _relay_again(stream: str, needle: str) -> bool:
    """XADD again the newest stream entry whose data contains ``needle`` (a
    duplicate delivery of the same platform event, as a second relay would)."""
    # Raw output (not a tty): id, "channel", <channel>, "data", <json> per entry.
    lines = _redis("XREVRANGE", stream, "+", "-", "COUNT", "300").splitlines()
    for i, ln in enumerate(lines):
        if needle in ln and i >= 3 and lines[i - 1] == "data" and lines[i - 3] == "channel":
            json.loads(ln)  # the entry's JSON payload, re-sent byte for byte
            _redis("XADD", stream, "*", "channel", lines[i - 2], "data", ln)
            return True
    return False


def _tenant_of_agent(agent_id: str) -> str:
    return _psql(f"SELECT tenant_id FROM agents WHERE id = '{agent_id}'")


@pytest.fixture
def other(second_tenant_api: LiveAPI | None) -> LiveAPI:
    if second_tenant_api is None:
        pytest.skip("needs RW_SECOND_TENANT_API_KEY / RW_SECOND_TENANT_FILE (isolation)")
    return second_tenant_api


# ── goal_completed ────────────────────────────────────────────────────────────


@pytest.mark.scenario("PLATFORM-GOAL-CHAIN")
def test_goal_completed_chain_fires_once_and_never_loops(
        api: LiveAPI, other: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    src = _agent(api, cleanup, "rw-b7-src")
    dst = _agent(api, cleanup, "rw-b7-dst")
    t1 = _trigger(api, cleanup, {"trigger_type": "goal_completed", "name": f"rw-b7-chain-{t}",
                                 "watch_agent_id": src},
                  "Write a one-line handover note for completed goal {{payload.goal_id}}." + ACK,
                  agent_id=dst)
    t2 = _trigger(api, cleanup, {"trigger_type": "goal_completed", "name": f"rw-b7-selfguard-{t}",
                                 "watch_agent_id": dst},
                  "Archive the handover of goal {{payload.goal_id}}." + ACK, agent_id=dst)
    # The other tenant watches EVERY completion of its own tenant.
    other_cleanup: list[str] = []
    resp = other.post("/triggers", json={"spec": {"trigger_type": "goal_completed",
                                                  "name": f"rw-b7-other-{t}"},
                                         "goal_template": "Other tenant follow-up." + ACK})
    assert resp.status_code == 201, mask(resp.text[:300])
    t_other = str(resp.json()["schedule_id"])
    other_cleanup.append(t_other)
    evidence.update(t1=t1, t2=t2, t_other=t_other)
    try:
        a = _submit(api, cleanup, f"Quarterly vendor check {t}: confirm receipt." + ACK,
                    agent_id=src)
        evidence["goal_a"] = a
        assert _wait_goal(api, a).get("status") == "complete"
        fired1 = _wait_fired(api, t1, 1)
        assert len(fired1) == 1, f"T1 fired {len(fired1)} times for one completion"
        b = str(fired1[0]["goal_id"])
        evidence["goal_b"] = b
        assert _src(fired1[0]) == a, fired1[0].get("payload")
        assert int((fired1[0].get("payload") or {}).get("trigger_chain_depth") or 0) == 1
        assert _goal(api, b).get("agent_id") in (dst, None, ""), _goal(api, b).get("agent_id")
        assert _wait_goal(api, b).get("status") == "complete"

        fired2 = _wait_fired(api, t2, 1)
        assert len(fired2) == 1 and _src(fired2[0]) == b, [e.get("payload") for e in fired2]
        c = str(fired2[0]["goal_id"])
        evidence["goal_c"] = c
        assert int((fired2[0].get("payload") or {}).get("trigger_chain_depth") or 0) == 2
        assert _wait_goal(api, c).get("status") == "complete"
        guard = _wait_skip(api, t2, "self_trigger")
        evidence["t2_skips"] = [(e.get("skip_reason"), _src(e)) for e in _skips(api, t2)]
        assert [_src(e) for e in guard] == [c], evidence["t2_skips"]
        time.sleep(15)
        assert len(_fired(api, t2)) == 1, "T2 re-fired on its own goal"
        assert len(_fired(api, t1)) == 1, "T1 fired on a goal of another agent"
        other_events = other.get(f"/triggers/{t_other}/events").json()
        evidence["other_tenant_events"] = len(other_events)
        assert other_events == [], "the other tenant's trigger fired on this tenant's goals"
        # The other tenant's own completion does fire its trigger (isolation, not deafness).
        og = other.post("/goals", json={"goal": f"Other tenant probe {t}." + ACK})
        assert og.status_code in (200, 201, 202), mask(og.text[:200])
        ogid = str(og.json().get("goal_id") or og.json().get("id"))
        wait_until(lambda: other.get(f"/triggers/{t_other}/events").json(), timeout=GOAL_TIMEOUT,
                   interval=5, desc="the other tenant's trigger to fire on its own goal",
                   done=lambda ev: any((e.get("payload") or {}).get("goal_id") == ogid
                                       for e in ev))
        assert len(_fired(api, t1)) == 1 and len(_fired(api, t2)) == 1
    finally:
        for sid in other_cleanup:
            for e in other.get(f"/triggers/{sid}/events").json() or []:
                if e.get("goal_id"):
                    other.post(f"/goals/{e['goal_id']}/cancel")
            other.delete(f"/triggers/{sid}")


@pytest.mark.scenario("PLATFORM-SELF-TRIGGER-DEPTH")
def test_allow_self_trigger_chain_stops_at_depth_cap(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    loop = _agent(api, cleanup, "rw-b7-loop")
    sid = _trigger(api, cleanup, {"trigger_type": "goal_completed", "name": f"rw-b7-loop-{t}",
                                  "watch_agent_id": loop, "allow_self_trigger": True},
                   "Chain link after goal {{payload.goal_id}}." + ACK, agent_id=loop)
    evidence["trigger_id"] = sid
    root = _submit(api, cleanup, f"Start of chain {t}." + ACK, agent_id=loop)
    evidence["root_goal"] = root
    try:
        skip = _wait_skip(api, sid, "chain_depth_exceeded", timeout=float(
            os.getenv("RW_CHAIN_TIMEOUT", "1500")))
    finally:
        fired = _fired(api, sid)
        evidence["fired"] = len(fired)
        evidence["depths"] = sorted(int((e.get("payload") or {}).get("trigger_chain_depth") or 0)
                                    for e in fired)
        evidence["skips"] = [e.get("skip_reason") for e in _skips(api, sid)]
    assert evidence["depths"] == list(range(1, MAX_CHAIN_DEPTH + 1)), evidence["depths"]
    assert len(skip) == 1, evidence["skips"]
    last = max(fired, key=lambda e: int((e.get("payload") or {}).get("trigger_chain_depth") or 0))
    assert _src(skip[0]) == str(last["goal_id"]), (skip[0].get("payload"), last["goal_id"])
    time.sleep(15)
    assert len(_fired(api, sid)) == MAX_CHAIN_DEPTH


# ── goal_failed ───────────────────────────────────────────────────────────────


def _failing_goal(t: str) -> str:
    return ("Read the JSON document at https://rw-b7-unreachable.invalid/status.json and report "
            "the exact value of its 'release_token' field. The value is random and cannot be "
            f"guessed (request {t}): if the document cannot be fetched, the task has FAILED - say "
            "so and do not invent a value.")


@pytest.mark.scenario("PLATFORM-GOAL-FAILED")
def test_goal_failed_fires_once_on_every_failure_path(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    worker_src = _agent(api, cleanup, "rw-b7-failsrc", max_iterations=1)
    beat_src = _agent(api, cleanup, "rw-b7-beatsrc")
    t_worker = _trigger(api, cleanup, {"trigger_type": "goal_failed",
                                       "name": f"rw-b7-failed-worker-{t}",
                                       "watch_agent_id": worker_src},
                        "Incident review for failed goal {{payload.goal_id}}." + ACK)
    t_beat = _trigger(api, cleanup, {"trigger_type": "goal_failed",
                                     "name": f"rw-b7-failed-beat-{t}",
                                     "watch_agent_id": beat_src},
                      "Incident review for failed goal {{payload.goal_id}}." + ACK)
    tenant = _tenant_of_agent(beat_src)
    ids = {k: f"rwb7{k}{uuid.uuid4().hex[:12]}" for k in ("stuck", "stale", "parked")}
    req_id = f"rwb7req{uuid.uuid4().hex[:12]}"
    evidence.update(t_worker=t_worker, t_beat=t_beat, synthetic=ids)
    try:
        # Beat paths: rows placed in the states the three sweeps look for.
        text = f"rw-b7 synthetic goal {t}"
        _psql(
            "INSERT INTO goals (id, tenant_id, agent_id, goal_text, status, updated_at, "
            "execution_context) VALUES "
            f"('{ids['stuck']}', '{tenant}', '{beat_src}', '{text} (stuck)', 'executing', "
            "now() - interval '3 days', '{}'), "
            f"('{ids['stale']}', '{tenant}', '{beat_src}', '{text} (stale runner)', 'executing', "
            "now(), '{\"watchdog_requeues\": 1}'), "
            f"('{ids['parked']}', '{tenant}', '{beat_src}', '{text} (parked)', 'waiting_human', "
            "now(), '{\"_suspended_for_approval\": true}')")
        _psql(f"UPDATE goals SET heartbeat_at = now() - interval '1 hour', runner_token = 'rw' "
              f"WHERE id = '{ids['stale']}'")
        _psql("INSERT INTO approval_requests (id, tenant_id, goal_id, action, risk_level, status, "
              f"expires_at) VALUES ('{req_id}', '{tenant}', '{ids['parked']}', 'rw-b7 deploy', "
              "'high', 'pending', now() - interval '1 minute')")

        # Worker path: a goal the agent loop fails (unfetchable data, one iteration).
        g = _submit(api, cleanup, _failing_goal(t), agent_id=worker_src)
        evidence["worker_goal"] = g
        final = _wait_goal(api, g)
        evidence["worker_goal_status"] = final.get("status")
        assert final.get("status") == "failed", f"the worker did not fail the goal: {final}"
        fired_w = _wait_fired(api, t_worker, 1)
        assert [_src(e) for e in fired_w] == [g], [e.get("payload") for e in fired_w]

        fired_b = _wait_fired(api, t_beat, 3, timeout=float(os.getenv("RW_BEAT_TIMEOUT", "540")))
        statuses = _psql(f"SELECT id || ':' || status FROM goals WHERE id IN "
                         f"('{ids['stuck']}', '{ids['stale']}', '{ids['parked']}') ORDER BY id")
        evidence["synthetic_status"] = statuses.splitlines()
        evidence["beat_fired"] = sorted(_src(e) for e in fired_b)
        assert sorted(_src(e) for e in fired_b) == sorted(ids.values()), evidence["beat_fired"]
        assert all(s.endswith(":failed") for s in statuses.splitlines()), statuses
        time.sleep(20)
        assert len(_fired(api, t_beat)) == 3 and len(_fired(api, t_worker)) == 1
    finally:
        _cancel_fired(api, cleanup, t_worker)
        _cancel_fired(api, cleanup, t_beat)
        with contextlib.suppress(Exception):
            _psql(f"DELETE FROM approval_requests WHERE id = '{req_id}'")
            _psql("DELETE FROM goals WHERE id IN "
                  f"('{ids['stuck']}', '{ids['stale']}', '{ids['parked']}')")


# ── goal_score_below ──────────────────────────────────────────────────────────


@pytest.mark.scenario("PLATFORM-SCORE-BELOW")
def test_goal_score_below_threshold_and_dimension(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    scored = _agent(api, cleanup, "rw-b7-scored")
    base = {"trigger_type": "goal_score_below", "watch_agent_id": scored}
    codes = {}
    for label, extra in (("none", {}), ("zero", {"score_threshold": 0}),
                         ("above_one", {"score_threshold": 1.5})):
        r = api.post("/triggers", json={"spec": {**base, "name": f"rw-b7-bad-{label}-{t}", **extra},
                                        "goal_template": "x" + ACK})
        codes[label] = r.status_code
        if r.status_code == 201:
            cleanup("DELETE", f"/triggers/{r.json()['schedule_id']}")
    evidence["invalid_create"] = codes
    assert codes == {"none": 422, "zero": 422, "above_one": 422}, codes
    cases = {  # name -> (dimension, threshold)
        "overall_high": ("", 0.97),
        "overall_low": ("", 0.05),
        "tool_relevance": ("tool_relevance", 0.9),
        "accuracy": ("accuracy", 0.5),
        "task_completion": ("task_completion", 0.5),
    }
    sids = {
        name: _trigger(api, cleanup, {**base, "name": f"rw-b7-score-{name}-{t}",
                                      "score_threshold": thr,
                                      **({"score_dimension": dim} if dim else {})},
                       "Review low-scoring goal {{payload.goal_id}} (score {{payload.score}})."
                       + ACK)
        for name, (dim, thr) in cases.items()
    }
    g = _submit(api, cleanup, f"Confirm ticket SCORE-{t} was received." + ACK, agent_id=scored)
    evidence["goal"] = g
    assert _wait_goal(api, g).get("status") == "complete"
    ev = wait_until(lambda: api.get(f"/goals/{g}/eval").json(), timeout=120, interval=4,
                    desc="the goal's eval scorecard", done=lambda b: bool(b.get("scores")))
    scores = {k: float(v) for k, v in (ev.get("scores") or {}).items()}
    avg = float(ev.get("average_score") or 0)
    evidence.update(scores=scores, average=avg)
    assert scores.get("task_completion") == 1.0, f"a completed goal scored {scores}"
    time.sleep(25)  # every trigger had its chance
    expect: dict[str, bool] = {}
    for name, (dim, thr) in cases.items():
        observed = scores.get(dim) if dim else avg
        expect[name] = observed is not None and observed < thr
    got = {name: len(_fired(api, sid)) for name, sid in sids.items()}
    evidence.update(expect=expect, fired=got)
    assert got == {n: (1 if e else 0) for n, e in expect.items()}, (got, expect, scores)
    assert any(expect.values()) and not all(expect.values()), expect
    for name, sid in sids.items():
        for e in _fired(api, sid):
            assert _src(e) == g, e.get("payload")
        _cancel_fired(api, cleanup, sid)


# ── hitl_approved / hitl_rejected ─────────────────────────────────────────────


def _pending(api: LiveAPI, goal_id: str) -> list[dict[str, Any]]:
    return [a for a in api.json_ok("GET", "/governance/approvals") or []
            if a.get("goal_id") == goal_id and a.get("status", "pending") == "pending"]


def _decide_all(api: LiveAPI, goal_id: str, verb: str, decided: list[str]) -> dict[str, Any]:
    """Decide every approval the goal raises until it ends (at most 3)."""
    deadline = time.monotonic() + GOAL_TIMEOUT
    while time.monotonic() < deadline:
        goal = _goal(api, goal_id)
        pending = _pending(api, goal_id)
        if pending and len(decided) < 3:
            rid = str(pending[0]["request_id"])
            r = api.post(f"/governance/approvals/{rid}/{verb}",
                         json={"approver": "rw-suite", "note": f"rw-b7 {verb}"})
            assert r.status_code == 200, f"{verb} -> {r.status_code}: {mask(r.text[:300])}"
            decided.append(rid)
            continue
        if goal.get("status") in TERMINAL or (decided and goal.get("status") in (
                "waiting_human",) and not pending):
            return goal
        time.sleep(4)
    return _goal(api, goal_id)


@pytest.mark.scenario("PLATFORM-HITL-GOAL")
def test_goal_approval_decisions_fire_hitl_triggers_once(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    agent = _agent(api, cleanup, "rw-b7-supervised", autonomy_mode="supervised", max_iterations=6,
                   system_prompt="You are a careful data-hygiene operator. Work only on data "
                                 "given in the goal; never call external systems.")
    queue = f"agent:{agent}"
    t_app = _trigger(api, cleanup, {"trigger_type": "hitl_approved", "name": f"rw-b7-app-{t}",
                                    "hitl_queue_id": queue},
                     "Record approval {{payload.request_id}} by {{payload.approver}}." + ACK)
    t_rej = _trigger(api, cleanup, {"trigger_type": "hitl_rejected", "name": f"rw-b7-rej-{t}",
                                    "hitl_queue_id": queue},
                     "Record rejection {{payload.request_id}}." + ACK)
    evidence.update(t_app=t_app, t_rej=t_rej)

    approved: list[str] = []
    g1 = _submit(api, cleanup, HIGH_RISK_GOAL.format(t=f"{t}a"), agent_id=agent)
    wait_until(lambda: _pending(api, g1) or _goal(api, g1).get("status") in TERMINAL,
               timeout=GOAL_TIMEOUT, interval=4, desc=f"an approval gate on goal {g1}")
    assert _pending(api, g1), f"no approval was raised: {_goal(api, g1).get('status')}"
    _decide_all(api, g1, "approve", approved)
    evidence["approved"] = approved
    fired = _wait_fired(api, t_app, len(approved))
    assert sorted(_src(e, "request_id") for e in fired) == sorted(approved), [
        e.get("payload") for e in fired]
    assert all(_src(e) == g1 for e in fired)
    # The same decision relayed again on the bus (a second relay / replica).
    assert _relay_again("trigger:stream:hitl", approved[0]), "approval event not on the stream"
    time.sleep(20)
    evidence["app_skips"] = [e.get("skip_reason") for e in _skips(api, t_app)]
    assert len(_fired(api, t_app)) == len(approved), "a relayed decision fired again"
    assert "dedup" in evidence["app_skips"], evidence["app_skips"]
    assert _fired(api, t_rej) == []

    rejected: list[str] = []
    g2 = _submit(api, cleanup, HIGH_RISK_GOAL.format(t=f"{t}b"), agent_id=agent)
    wait_until(lambda: _pending(api, g2) or _goal(api, g2).get("status") in TERMINAL,
               timeout=GOAL_TIMEOUT, interval=4, desc=f"an approval gate on goal {g2}")
    assert _pending(api, g2), f"no approval was raised: {_goal(api, g2).get('status')}"
    _decide_all(api, g2, "reject", rejected)
    evidence["rejected"] = rejected
    fired_r = _wait_fired(api, t_rej, len(rejected))
    assert sorted(_src(e, "request_id") for e in fired_r) == sorted(rejected)
    assert len(_fired(api, t_app)) == len(approved)
    _cancel_fired(api, cleanup, t_app)
    _cancel_fired(api, cleanup, t_rej)


@pytest.mark.scenario("PLATFORM-HITL-WORKFLOW")
def test_workflow_approval_decisions_fire_hitl_triggers_once(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    wf = wfx.create_workflow(api, cleanup, prefix="rw-b7-wf-hitl")
    wf_id = str(wf["id"])
    cond = f'payload.workflow_id == "{wf_id}"'
    t_app = _trigger(api, cleanup, {"trigger_type": "hitl_approved", "name": f"rw-b7-wapp-{t}",
                                    "condition_cel": cond},
                     "Publish note for approved run {{payload.workflow_run_id}}." + ACK)
    t_rej = _trigger(api, cleanup, {"trigger_type": "hitl_rejected", "name": f"rw-b7-wrej-{t}",
                                    "condition_cel": cond},
                     "Rework note for rejected run {{payload.workflow_run_id}}." + ACK)
    evidence.update(workflow_id=wf_id, t_app=t_app, t_rej=t_rej)

    def park() -> tuple[str, str]:
        run_id = str(api.json_ok("POST", f"{wfx.V1}/workflows/{wf_id}/trigger",
                                 json={"inputs": {"team": "Payments", "week": "2026-W41",
                                                  "highlights": "Closed 9 of 11 tickets."}}
                                 )["run_id"])
        cleanup("POST", f"{wfx.V1}/runs/{run_id}/cancel")
        run = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
        assert run.get("status") == "waiting_hitl", run.get("status")
        return run_id, str(wfx.find_approval(api, run_id)["request_id"])

    run1, req1 = park()
    wfx.decide(api, req1, "approve", "Numbers verified.")
    again = api.post(f"{wfx.V1}/approvals/{req1}/decide",
                     json={"action": "approve", "note": "double click"})
    evidence["repeat_decide"] = again.status_code
    fired = _wait_fired(api, t_app, 1)
    assert [_src(e, "request_id") for e in fired] == [req1], [e.get("payload") for e in fired]
    assert _src(fired[0], "workflow_run_id") == run1
    assert _relay_again("trigger:stream:hitl", req1), "workflow approval event not on the stream"
    time.sleep(20)
    evidence["app_skips"] = [e.get("skip_reason") for e in _skips(api, t_app)]
    assert len(_fired(api, t_app)) == 1, "a repeated / relayed decision fired again"
    assert _fired(api, t_rej) == []

    run2, req2 = park()
    wfx.decide(api, req2, "reject", "Figures do not reconcile.")
    fired_r = _wait_fired(api, t_rej, 1)
    assert [_src(e, "request_id") for e in fired_r] == [req2]
    assert _src(fired_r[0], "workflow_run_id") == run2
    assert len(_fired(api, t_app)) == 1
    _cancel_fired(api, cleanup, t_app)
    _cancel_fired(api, cleanup, t_rej)


# ── memory_created ────────────────────────────────────────────────────────────


@pytest.mark.scenario("PLATFORM-MEMORY")
def test_memory_created_fires_once_and_does_not_loop(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    t = tag()
    agent = _agent(api, cleanup, "rw-b7-mem")
    g = _submit(api, cleanup, f"Note for the record: supplier SUP-{t} prefers invoices by email."
                + ACK, agent_id=agent)
    # Scoped to this goal's learnings; the loop guard runs BEFORE the condition,
    # so the learning of the trigger's own goal is audited self_trigger.
    sid = _trigger(api, cleanup, {"trigger_type": "memory_created", "name": f"rw-b7-mem-{t}",
                                  "memory_type": "success_pattern",
                                  "condition_cel": f'payload.source_goal_id == "{g}"'},
                   "Index the new learning {{payload.memory_id}}." + ACK)
    evidence.update(goal=g, trigger_id=sid)
    assert _wait_goal(api, g).get("status") == "complete"
    fired = _wait_fired(api, sid, 1)
    assert len(fired) == 1 and _src(fired[0], "source_goal_id") == g, [
        e.get("payload") for e in fired]
    own = str(fired[0]["goal_id"])
    evidence["own_goal"] = own
    assert _wait_goal(api, own).get("status") == "complete"
    guard = _wait_skip(api, sid, "self_trigger", timeout=180)
    assert [_src(e, "source_goal_id") for e in guard] == [own], [e.get("payload") for e in guard]
    time.sleep(15)
    assert len(_fired(api, sid)) == 1, "the trigger re-fired on its own goal's learning"
    mem_id = _src(fired[0], "memory_id")
    assert _relay_again("trigger:stream:memory", mem_id), "memory event not on the stream"
    time.sleep(20)
    evidence["skips"] = [e.get("skip_reason") for e in _skips(api, sid)]
    assert len(_fired(api, sid)) == 1, "a relayed memory event fired again"


# ── quota / rate ──────────────────────────────────────────────────────────────


@pytest.mark.scenario("PLATFORM-QUOTA-RATE")
def test_platform_trigger_quota_and_rate_cap(
        api: LiveAPI, other: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cap = int(os.getenv("RW_FREE_TRIGGER_CAP", "5"))
    existing = other.get("/triggers").json()
    made: list[str] = []
    codes: list[int] = []
    try:
        for i in range(cap - len(existing) + 1):
            r = other.post("/triggers", json={
                "spec": {"trigger_type": ("goal_completed", "memory_created", "hitl_rejected")[
                    i % 3], "name": f"rw-b7-quota-{i}-{tag()}"},
                "goal_template": "quota probe" + ACK})
            codes.append(r.status_code)
            if r.status_code == 201:
                made.append(r.json()["schedule_id"])
    finally:
        for s in made:
            other.delete(f"/triggers/{s}")
    evidence.update(existing=len(existing), codes=codes)
    assert codes[-1] == 403 and all(c == 201 for c in codes[:-1]), codes

    t = tag()
    agent = _agent(api, cleanup, "rw-b7-rate")
    sid = _trigger(api, cleanup, {"trigger_type": "goal_completed", "name": f"rw-b7-rate-{t}",
                                  "watch_agent_id": agent, "max_firings_per_hour": 1},
                   "Rate probe after {{payload.goal_id}}." + ACK)
    goals = [_submit(api, cleanup, f"Rate probe {t} #{n}." + ACK, agent_id=agent)
             for n in (1, 2)]
    for g in goals:
        assert _wait_goal(api, g).get("status") == "complete"
    fired = _wait_fired(api, sid, 1)
    _wait_skip(api, sid, "rate_limit", timeout=120)
    assert len(fired) == 1, f"the 1/h cap let {len(fired)} firings through"

    def dlq_rows() -> list[dict[str, Any]]:
        return [d for d in api.get("/triggers/dlq").json() if d.get("trigger_id") == sid]

    rows = wait_until(dlq_rows, timeout=60, interval=3, desc="the throttled firing in the DLQ")
    evidence["dlq"] = [r.get("failure_type") for r in rows]
    assert [r.get("failure_type") for r in rows] == ["RATE_LIMITED"], rows
    _cancel_fired(api, cleanup, sid)


# ── replicas ──────────────────────────────────────────────────────────────────


@pytest.mark.scenario("PLATFORM-MULTI-REPLICA")
def test_goal_events_fire_once_with_two_api_replicas(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    if not SECOND_BACKEND:
        pytest.skip("needs RW_SECOND_BACKEND_CONTAINER (a second API replica on the bus)")
    state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", SECOND_BACKEND],
                           capture_output=True, text=True, timeout=30).stdout.strip()
    if state != "true":
        pytest.skip(f"{SECOND_BACKEND} is not running")
    t = tag()
    agent = _agent(api, cleanup, "rw-b7-replica")
    sid = _trigger(api, cleanup, {"trigger_type": "goal_completed", "name": f"rw-b7-rep-{t}",
                                  "watch_agent_id": agent},
                   "Replica follow-up for {{payload.goal_id}}." + ACK)
    n = int(os.getenv("RW_REPLICA_GOALS", "4"))
    goals = [_submit(api, cleanup, f"Replica probe {t} #{i}." + ACK, agent_id=agent)
             for i in range(n)]
    for g in goals:
        assert _wait_goal(api, g).get("status") == "complete"
    fired = _wait_fired(api, sid, n, settle=20)
    evidence["consumers"] = _redis("XINFO", "CONSUMERS", "trigger:stream:goal",
                                   "trigger-consumer:chain").count("name")
    assert sorted(_src(e) for e in fired) == sorted(goals), [_src(e) for e in fired]
    _cancel_fired(api, cleanup, sid)
