"""CHAOS-*: real faults injected into the live stack; recovery, exactly-once, no data loss.

Opt-in: ``RW_CHAOS=1`` plus the exact container name each scenario needs (never
guessed): ``RW_WORKER_CONTAINER`` (ingestion worker), ``RW_WORKFLOW_WORKER_CONTAINER``,
``RW_SCHEDULE_WORKER_CONTAINER``, ``RW_BACKEND_CONTAINER``, ``RW_REDIS_CONTAINER``,
``RW_PG_CONTAINER``, ``RW_PGBOUNCER_CONTAINER``, ``RW_MONGO_CONTAINER``. Every fault is a
real ``docker kill / stop / pause / restart`` undone in a ``finally`` (``chaos.fault``),
and each scenario waits for the container (and the API) to be healthy before it
asserts. Side effects are counted in a real external system: documents in MongoDB
(``rw_shop.<ledger>``) written by the workflow's platform-gated ``mongodb_insert_one``.

Each scenario asserts recovery, no duplicate side effects, no data loss, the right
final workflow / goal / job state and the audit trail:

* CHAOS-INGEST-WORKER-KILL — SIGKILL the ingestion worker mid MongoDB sync: the sync is
  resumed / requeued and completes with the exact document set and zero duplicates.
* CHAOS-MONGO-RESTART — restart MongoDB mid sync: retried; the KB ends exact.
* CHAOS-WORKFLOW-WORKER-KILL — SIGKILL the workflow worker right after the side-effecting
  step: the run resumes / is requeued and the side effect happened exactly once.
* CHAOS-REDIS-PAUSE — pause Redis ~12 s under a running workflow and goal: both finish
  correctly, one side effect, audit intact.
* CHAOS-PG-OUTAGE — stop Postgres / PgBouncer briefly: the API answers 503 (never 500,
  never a wrong 200), recovers, and every acknowledged write survives.
* CHAOS-API-KILL-SSE — kill the API during a goal's SSE stream: reconnect with
  ``Last-Event-ID`` replays without gaps or duplicates.
* CHAOS-TRIGGER-DEDUP — duplicate and out-of-order trigger events (with the schedule
  worker restarted mid-stream when ``RW_SCHEDULE_WORKER_CONTAINER`` is set): one goal per
  event id.
* CHAOS-MODEL-OUTAGE — the preferred chat model really unreachable (an unroutable address
  in the Model Registry): goals fail over to the next eligible real model, the breaker
  makes later calls fast, removing the dead entry recovers.

Timing knobs: ``RW_CHAOS_RECOVERY_TIMEOUT`` (default 1800 s: the stuck-run redispatch runs
every 5 min after 10 min idle), ``RW_CHAOS_DOWN_S``, ``RW_REDIS_PAUSE_S``,
``RW_PG_OUTAGE_S``, ``RW_CHAOS_SYNC_DOCS``, ``RW_CHAOS_UNROUTABLE_URL``;
``RW_CHAOS_ALLOW_MANUAL_RESYNC=1`` accepts an operator re-sync after a dead job.
"""

from __future__ import annotations

import contextlib
import os
import random
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import chaos
from tests.real_world import commerce_seed as cs
from tests.real_world import live_mongo as lm
from tests.real_world import onprem as op
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world import wf_mongo as wfm
from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, env_float, load_api_key, mask, tag, wait_until
from tests.real_world.metrics import record
from tests.real_world.sse_stream import StreamReader, gaps_and_duplicates

RECOVERY = env_float("RW_CHAOS_RECOVERY_TIMEOUT", 1800)
DOWN_S = env_float("RW_CHAOS_DOWN_S", 20)
REDIS_PAUSE_S = env_float("RW_REDIS_PAUSE_S", 12)
PG_OUTAGE_S = env_float("RW_PG_OUTAGE_S", 15)
SYNC_DOCS = int(os.getenv("RW_CHAOS_SYNC_DOCS", "4000"))
GOAL_TIMEOUT = env_float("RW_GOAL_TIMEOUT", 480)
UNROUTABLE = os.getenv("RW_CHAOS_UNROUTABLE_URL", "http://10.255.255.1:8000/v1")
DB = lm.SOURCE_DB


@pytest.fixture(autouse=True)
def _chaos_gate() -> Iterator[None]:
    chaos.require_chaos()
    yield
    owed = chaos.undo_all()  # belt and braces: nothing stays stopped / paused
    assert not owed, f"faults were still injected after the scenario: {owed}"


@pytest.fixture
def mongo() -> Iterator[Any]:
    client = lm.seed_client()
    yield client
    client.close()


def _seeded_source(api: LiveAPI, cleanup: Any, mongo: Any, size: int, prefix: str
                   ) -> dict[str, Any]:
    t = tag()
    data = cs.generate(size)
    names = cs.seed(mongo, DB, t, data)
    cleanup_names = dict(names)
    cid = srcs.create_collection(api, cleanup, prefix)
    cfg = lm.reader_config(list(names.values()), batch_size=100,
                           max_documents_per_sync=data.total + 1000)
    sid = lm.create_source(api, cleanup, cid, cfg)["id"]
    expected = lm.expected_tails(DB, names, {lg: data.keys(lg) for lg in cs.LOGICAL})
    return {"data": data, "names": cleanup_names, "cid": cid, "sid": sid, "expected": expected}


def _converge(api: LiveAPI, sid: str, cid: str, expected: set[str], job_id: str,
              evidence: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Wait for the interrupted job (or an automatic successor) to finish the work."""
    started_iso = lm.utc_iso()
    manual = os.getenv("RW_CHAOS_ALLOW_MANUAL_RESYNC") == "1"
    path: list[str] = []

    def probe() -> dict[str, Any]:
        mine = sj.job(api, sid, job_id) or {}
        status = str(mine.get("status", "")).lower()
        if status in sj.TERMINAL and status not in sj.COMPLETED and manual and \
                "manual" not in path:
            path.append("manual")
            sj.trigger(api, sid)
        inv = lm.inventory(api, sid, cid, expected)
        jobs = sj.jobs(api, sid)
        return {"job": {k: mine.get(k) for k in ("status", "docs_indexed", "error_message")},
                "running": [j.get("status") for j in jobs if str(j.get("status")).lower()
                            in ("running", "pending", "queued")],
                "inv": inv, "exact": not lm.exactness_problems(inv)}

    final = wait_until(probe, timeout=RECOVERY, interval=15, desc="the sync to converge",
                       done=lambda s: s["exact"] and not s["running"])
    evidence["recovery_path"] = path or ["automatic"]
    evidence["recovered_jobs"] = [(j.get("job_id"), j.get("status"), j.get("docs_indexed"),
                                   j.get("triggered_by")) for j in lm.jobs_since(
                                       api, sid, started_iso)]
    return final, lm.exactness_problems(final["inv"])


# ── CHAOS-INGEST-WORKER-KILL ────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-INGEST-WORKER-KILL")
def test_kill_ingestion_worker_mid_sync(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                        mongo: Any) -> None:
    worker = chaos.require_container("RW_WORKER_CONTAINER")
    s = _seeded_source(api, cleanup, mongo, SYNC_DOCS, "rw-chaos-ingest")
    try:
        job_id = sj.trigger(api, s["sid"])
        seen = lm.wait_running_job(api, s["sid"], job_id, min_indexed=max(50, SYNC_DOCS // 10),
                                   timeout=1800)
        evidence.update(source_id=s["sid"], job_id=job_id, indexed_at_kill=seen.get("docs_indexed"))
        assert str(seen.get("status")).lower() not in sj.TERMINAL, \
            f"the sync finished before the fault could be injected: {seen}"
        started = time.monotonic()
        with chaos.fault("kill", worker, hold_s=DOWN_S, evidence=evidence):
            pass
        evidence["api_back_s"] = chaos.wait_api()
        final, problems = _converge(api, s["sid"], s["cid"], s["expected"], job_id, evidence)
        evidence["inventory"] = {k: v for k, v in final["inv"].items() if k != "content_hash"}
        record(evidence, documents=s["data"].total, indexed_at_kill=seen.get("docs_indexed"),
               recovery_s=round(time.monotonic() - started, 1))
        assert not problems, "; ".join(problems)
    finally:
        with contextlib.suppress(Exception):
            cs.drop(mongo, DB, s["names"])


# ── CHAOS-MONGO-RESTART ─────────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-MONGO-RESTART")
def test_restart_mongodb_mid_sync(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                  mongo: Any) -> None:
    mongo_container = chaos.require_container("RW_MONGO_CONTAINER")
    s = _seeded_source(api, cleanup, mongo, max(1500, SYNC_DOCS // 2), "rw-chaos-mongo")
    try:
        job_id = sj.trigger(api, s["sid"])
        seen = lm.wait_running_job(api, s["sid"], job_id, min_indexed=100, timeout=1800)
        evidence.update(source_id=s["sid"], job_id=job_id, indexed_at_fault=seen.get("docs_indexed"))
        with chaos.fault("restart", mongo_container, evidence=evidence):
            pass
        first = sj.wait_job(api, s["sid"], job_id, timeout=RECOVERY)
        evidence["interrupted_job"] = sj.mask_job(first)
        soft: list[str] = []
        status = str(first.get("status")).lower()
        inv_now = lm.inventory(api, s["sid"], s["cid"], s["expected"])
        if status in sj.COMPLETED and lm.exactness_problems(inv_now):
            soft.append(f"the interrupted job reported completed but the KB is not exact: "
                        f"{lm.exactness_problems(inv_now)[:2]}")
        if status not in sj.COMPLETED:
            err = str(first.get("error_message") or "")
            if "error id" not in err:
                soft.append(f"the interrupted job failed without an error id: {err[:160]}")
            retry = sj.sync(api, s["sid"], timeout=RECOVERY)  # the documented retry path
            evidence["retry_job"] = retry
            if str(retry.get("status")).lower() not in sj.COMPLETED:
                soft.append(f"the retry after the restart {retry.get('status')}: "
                            f"{retry.get('error_message')}")
        inv = lm.inventory(api, s["sid"], s["cid"], s["expected"])
        evidence["inventory"] = {k: v for k, v in inv.items() if k != "content_hash"}
        soft += lm.exactness_problems(inv)
        record(evidence, documents=s["data"].total, self_healed=status in sj.COMPLETED)
        assert not soft, "; ".join(soft)
    finally:
        with contextlib.suppress(Exception):
            cs.drop(mongo, DB, s["names"])


# ── shared workflow fixture for the side-effect scenarios ───────────────────


def _kb(api: LiveAPI, cleanup: Any) -> str:
    cid = srcs.create_collection(api, cleanup, "rw-chaos-kb")
    data = cs.generate(300)
    for doc in data.docs["postmortems"][:3]:
        text = (f"Incident {doc['_id']}: {doc['title']}. Severity {doc['severity']}. "
                f"{doc['summary']} Root cause: {doc['root_cause']} Duration "
                f"{doc['duration_minutes']} minutes. Action items: "
                + "; ".join(f"{a['item']} (owner {a['owner']})" for a in doc["action_items"]))
        api.json_ok("POST", "/knowledge/ingest", json={
            "collection_id": cid, "source_type": "text", "content": text,
            "metadata": {"title": f"postmortem-{doc['_id']}"}})
    return cid


def _side_effect_run(api: LiveAPI, cleanup: Any, mongo: Any, evidence: dict[str, Any]
                     ) -> dict[str, Any]:
    t = tag()
    ledger = f"chaos_ledger_{t}"
    server = lm.register_connector(api, cleanup, f"commerce-db-{t}", lm.tool_dsn())
    cid = _kb(api, cleanup)
    wf_id = wfx.import_yaml(api, cleanup, wfm.side_effect_then_llm_yaml(
        f"rw-chaos-settlement-{t}", server_id=server["server_id"], ledger=ledger,
        collection_id=cid))
    run_id = wfx.trigger_run(api, cleanup, wf_id)
    evidence.update(workflow_id=wf_id, run_id=run_id, ledger=ledger)
    return {"run_id": run_id, "wf_id": wf_id, "col": mongo[lm.TOOL_DB][ledger]}


def _approve_side_effect_once(api: LiveAPI, ctx: dict[str, Any], evidence: dict[str, Any]
                              ) -> None:
    """Approve the record_hold write exactly once and wait until it is written."""
    approved: list[str] = []

    def probe() -> dict[str, Any]:
        for item in wfx.pending_for_run(api, ctx["run_id"]):
            if item.get("step_id") == "record_hold" and not approved:
                wfx.decide(api, str(item["request_id"]), "approve", "Hold confirmed by ops.")
                approved.append(str(item["request_id"]))
        steps = wfx.get_steps(api, ctx["run_id"])
        return {"written": ctx["col"].count_documents({"run_id": ctx["run_id"]}),
                "step": (steps.get("record_hold") or {}).get("status"),
                "run": wfx.get_run(api, ctx["run_id"]).get("status")}

    state = wait_until(probe, timeout=wfx.GATE_TIMEOUT, interval=1.0,
                       desc="the side effect written",
                       done=lambda s: s["written"] >= 1 or s["run"] in wfx.TERMINAL)
    evidence["side_effect_written"] = state
    ctx["approved"] = approved
    assert state["written"] == 1, f"the side effect was not written once: {state}"


def _finish_rejecting_repeats(api: LiveAPI, ctx: dict[str, Any], evidence: dict[str, Any]
                              ) -> dict[str, Any]:
    """Wait for the run to end; a SECOND approval for the side effect (a re-execution) is
    rejected and recorded — never approved."""
    repeats: list[str] = []

    def probe() -> dict[str, Any]:
        for item in wfx.pending_for_run(api, ctx["run_id"]):
            rid = str(item["request_id"])
            if item.get("step_id") == "record_hold" and rid not in ctx["approved"] and \
                    rid not in repeats:
                repeats.append(rid)
                wfx.decide(api, rid, "reject", "Duplicate side-effect request after a crash.")
        return wfx.get_run(api, ctx["run_id"])

    run = wait_until(probe, timeout=RECOVERY, interval=10, desc="the run to finish",
                     done=lambda r: str(r.get("status")) in wfx.TERMINAL)
    evidence["repeat_side_effect_requests"] = repeats
    return dict(run)


def _workflow_audit(api: LiveAPI, run_id: str) -> dict[str, int]:
    names = [str(r.get("tool_name")) for r in wfx.audit_rows(api, run_id)]
    return {n: names.count(n) for n in sorted(set(names))}


# ── CHAOS-WORKFLOW-WORKER-KILL ──────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-WORKFLOW-WORKER-KILL")
def test_kill_workflow_worker_between_steps(api: LiveAPI, cleanup: Any,
                                            evidence: dict[str, Any], mongo: Any) -> None:
    worker = chaos.require_container("RW_WORKFLOW_WORKER_CONTAINER")
    ctx = _side_effect_run(api, cleanup, mongo, evidence)
    try:
        _approve_side_effect_once(api, ctx, evidence)
        at_kill = wfx.get_run(api, ctx["run_id"]).get("status")
        evidence["run_status_at_kill"] = at_kill
        assert at_kill not in wfx.TERMINAL, (
            f"the run finished ({at_kill}) before the worker could be killed")
        started = time.monotonic()
        with chaos.fault("kill", worker, hold_s=DOWN_S, evidence=evidence):
            pass
        run = _finish_rejecting_repeats(api, ctx, evidence)
        steps = wfx.get_steps(api, ctx["run_id"])
        writes = ctx["col"].count_documents({"run_id": ctx["run_id"]})
        audit = _workflow_audit(api, ctx["run_id"])
        evidence.update(final=run.get("status"), writes=writes, audit=audit,
                        steps={k: v.get("status") for k, v in steps.items()},
                        run_metadata=mask(run.get("run_metadata"))[:300])
        soft: list[str] = []
        if run.get("status") != "complete":
            soft.append(f"the run ended {run.get('status')} after the worker was killed: "
                        f"{mask(run.get('error'))[:160]}")
        if writes != 1:
            soft.append(f"the side effect happened {writes} times (exactly once expected)")
        if evidence["repeat_side_effect_requests"]:
            soft.append("the completed side-effect step was re-executed after the crash "
                        f"({len(evidence['repeat_side_effect_requests'])} new approval requests)")
        if not audit.get("workflow.run.completed") and run.get("status") == "complete":
            soft.append("no workflow.run.completed audit row")
        if audit.get("workflow.run.completed", 0) > 1:
            soft.append("the run was audited as completed more than once")
        record(evidence, recovery_s=round(time.monotonic() - started, 1), writes=writes)
        assert not soft, "; ".join(soft)
    finally:
        with contextlib.suppress(Exception):
            ctx["col"].drop()


# ── CHAOS-REDIS-PAUSE ───────────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-REDIS-PAUSE")
def test_pause_redis_under_running_work(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                        mongo: Any) -> None:
    redis = chaos.require_container("RW_REDIS_CONTAINER")
    ctx = _side_effect_run(api, cleanup, mongo, evidence)
    goal = api.json_ok("POST", "/goals", json={
        "goal": "Compute 17 multiplied by 23 and reply with just the number. Do not use tools."})
    goal_id = str(goal.get("goal_id") or goal.get("id"))
    cleanup("POST", f"/goals/{goal_id}/cancel")
    evidence["goal_id"] = goal_id
    try:
        _approve_side_effect_once(api, ctx, evidence)
        with chaos.fault("pause", redis, hold_s=REDIS_PAUSE_S, evidence=evidence):
            pass
        evidence["api_back_s"] = chaos.wait_api()
        run = _finish_rejecting_repeats(api, ctx, evidence)
        g = op.wait_goal(api, goal_id, GOAL_TIMEOUT)
        writes = ctx["col"].count_documents({"run_id": ctx["run_id"]})
        audit = _workflow_audit(api, ctx["run_id"])
        goal_audit = api.get(f"/goals/{goal_id}/audit")
        evidence.update(final=run.get("status"), writes=writes, audit=audit,
                        goal_status=g.get("status"), goal_answer=op.goal_text(g)[:160],
                        goal_audit_http=goal_audit.status_code)
        soft: list[str] = []
        if run.get("status") != "complete":
            soft.append(f"the workflow ended {run.get('status')} after the Redis pause")
        if writes != 1:
            soft.append(f"the side effect happened {writes} times")
        if g.get("status") not in ("complete", "completed"):
            soft.append(f"the goal ended {g.get('status')}: {mask(g.get('error'))[:160]}")
        elif "391" not in op.goal_text(g):
            soft.append(f"the goal's answer lost the result: {op.goal_text(g)[:120]}")
        for needed in ("workflow.run.started", "workflow.run.completed"):
            if not audit.get(needed):
                soft.append(f"audit trail lost {needed}")
        if audit.get("workflow.step.completed", 0) < 3:
            soft.append(f"only {audit.get('workflow.step.completed', 0)} step-completed rows")
        assert not soft, "; ".join(soft)
    finally:
        with contextlib.suppress(Exception):
            ctx["col"].drop()


# ── CHAOS-PG-OUTAGE ─────────────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-PG-OUTAGE")
@pytest.mark.parametrize("var", ["RW_PG_CONTAINER", "RW_PGBOUNCER_CONTAINER"])
def test_database_outage_answers_503_and_keeps_writes(api: LiveAPI, cleanup: Any,
                                                      evidence: dict[str, Any], var: str) -> None:
    container = chaos.require_container(var)
    before = srcs.create_collection(api, cleanup, "rw-chaos-pg-before")
    acknowledged: list[str] = [before]
    probes: list[dict[str, Any]] = []

    def probe() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for label, method, path, kw in (
                ("list collections", "GET", "/knowledge/collections", {}),
                ("list runs", "GET", "/api/v1/runs", {"params": {"per_page": 5}}),
                ("list sources", "GET", "/sources", {}),
                ("create collection", "POST", "/knowledge/collections",
                 {"json": {"name": f"rw-chaos-pg-during-{tag()}", "embedder_type": "default"}})):
            try:
                resp = api.client.request(method, path, timeout=20, **kw)
                out[label] = resp.status_code
                if label == "create collection" and resp.status_code in (200, 201):
                    cid = str(resp.json().get("collection_id"))
                    acknowledged.append(cid)
                    cleanup("DELETE", f"/knowledge/collections/{cid}")
                if label == "list collections" and resp.status_code == 200:
                    body = resp.json()
                    cols = body.get("collections", body) if isinstance(body, dict) else body
                    ids = {str(c.get("collection_id") or c.get("id")) for c in cols or []}
                    out["wrong_200"] = before not in ids
            except Exception as exc:  # a dropped connection is an answer too
                out[label] = type(exc).__name__
        return out

    with chaos.fault("stop", container, evidence=evidence):
        probes = chaos.probe_during(probe, PG_OUTAGE_S, interval=2)
    evidence["api_back_s"] = chaos.wait_api()
    ready = wait_until(lambda: api.client.get("/health/ready", timeout=10).status_code,
                       timeout=300, interval=3, desc="/health/ready 200", done=lambda c: c == 200)
    codes = [v for p in probes for k, v in p.items() if k not in ("t", "wrong_200")]
    tally = chaos.classify_outage_answers(codes)
    wrong = [p for p in probes if p.get("wrong_200")]
    evidence.update(probes=probes[:20], tally=tally, ready=ready, acknowledged=len(acknowledged))
    soft: list[str] = []
    if tally["500"]:
        soft.append(f"{tally['500']} answers were 500 during the outage (expected 503)")
    if wrong:
        soft.append(f"{len(wrong)} 200 answers during the outage listed wrong data")
    if not tally["503"] and not tally["2xx"]:
        soft.append(f"no 503 during a {PG_OUTAGE_S:.0f}s outage: {tally}")
    listed = api.json_ok("GET", "/knowledge/collections")
    cols = listed.get("collections", listed) if isinstance(listed, dict) else listed
    ids = {str(c.get("collection_id") or c.get("id")) for c in cols or []}
    lost = [c for c in acknowledged if c not in ids]
    if lost:
        soft.append(f"{len(lost)} acknowledged writes lost after the outage")
    after = api.post("/knowledge/collections", json={"name": f"rw-chaos-pg-after-{tag()}",
                                                     "embedder_type": "default"})
    if after.status_code in (200, 201):
        cleanup("DELETE", f"/knowledge/collections/{after.json().get('collection_id')}")
    else:
        soft.append(f"writes still fail after recovery: {after.status_code}")
    record(evidence, outage_s=PG_OUTAGE_S, answers_503=tally["503"], answers_500=tally["500"],
           acknowledged_writes=len(acknowledged))
    assert not soft, "; ".join(soft)


# ── CHAOS-API-KILL-SSE ──────────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-API-KILL-SSE")
def test_kill_api_during_sse_stream_replays_by_sequence(api: LiveAPI, cleanup: Any,
                                                        evidence: dict[str, Any]) -> None:
    backend = chaos.require_container("RW_BACKEND_CONTAINER")
    key = load_api_key()
    body = api.json_ok("POST", "/goals", json={
        "goal": "Plan, step by step, a five-item pre-monsoon readiness checklist for a "
                "warehouse in Kochi (drainage, roofing, pallets, power backup, staff), then "
                "summarise it in five bullet points. Do not use tools."})
    goal_id = str(body.get("goal_id") or body.get("id"))
    cleanup("POST", f"/goals/{goal_id}/cancel")
    path = f"/goals/{goal_id}/stream"
    first = StreamReader(key, path).start()
    wait_until(lambda: len(first.ids), timeout=GOAL_TIMEOUT, interval=1,
               desc="3 sequenced SSE events before the kill", done=lambda n: n >= 3
               or not first.alive())
    evidence.update(goal_id=goal_id, events_before_kill=len(first.ids))
    with chaos.fault("kill", backend, hold_s=3, evidence=evidence):
        pass
    evidence["api_back_s"] = chaos.wait_api()
    first.join(30)
    first.stop()
    second = StreamReader(key, path, last_event_id=first.last_id).start()
    goal = op.wait_goal(api, goal_id, GOAL_TIMEOUT)
    second.join(120)
    second.stop()
    full = StreamReader(key, path, last_event_id=0).start()
    full.join(120)
    full.stop()
    report = gaps_and_duplicates(first.ids, second.ids, full.ids)
    evidence.update(goal_status=goal.get("status"), first_ids=first.ids[-5:],
                    second_ids=second.ids[:5], full=len(full.ids), report=report,
                    reconnect_status=second.status_code, first_error=first.error)
    soft: list[str] = []
    if second.status_code != 200:
        soft.append(f"reconnect answered {second.status_code}: {second.error}")
    for k, v in report.items():
        if v:
            soft.append(f"{k}: {v[:10]}")
    if goal.get("status") not in ("complete", "completed", "failed", "cancelled"):
        soft.append(f"the goal never reached a terminal status: {goal.get('status')}")
    if not full.ids:
        soft.append("the full replay returned no sequenced events")
    record(evidence, events_total=len(full.ids), events_before_kill=len(first.ids),
           events_after_reconnect=len(second.ids))
    assert not soft, "; ".join(soft)


# ── CHAOS-TRIGGER-DEDUP ─────────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-TRIGGER-DEDUP")
def test_duplicate_out_of_order_trigger_events(api: LiveAPI, cleanup: Any,
                                               evidence: dict[str, Any]) -> None:
    t = tag()
    channel = f"rw.chaos.{t}"
    resp = api.post("/triggers", json={
        "spec": {"trigger_type": "event", "name": f"rw-chaos-dedup-{t}", "event_channel": channel},
        "goal_template": "Acknowledge settlement event {{payload.n}}. Reply with exactly the "
                         "word ACK. Do not use any tools."})
    assert resp.status_code == 201, f"POST /triggers -> {resp.status_code}: {mask(resp.text)}"
    sid = resp.json()["schedule_id"]
    cleanup("DELETE", f"/triggers/{sid}")
    unique = [f"evt-{t}-{n}" for n in range(6)]
    stream = [(eid, n) for n, eid in enumerate(unique)] * 3  # every event three times
    random.Random(t).shuffle(stream)  # and out of order
    restart = os.getenv("RW_SCHEDULE_WORKER_CONTAINER", "").strip()
    codes: list[int] = []
    for i, (eid, n) in enumerate(stream):
        if restart and i == len(stream) // 2:
            chaos.require_container("RW_SCHEDULE_WORKER_CONTAINER")
            with chaos.fault("restart", restart, evidence=evidence):
                pass
        codes.append(api.post(f"/triggers/events/{channel}",
                              json={"event_id": eid, "n": n}).status_code)

    def fired() -> list[dict[str, Any]]:
        r = api.get(f"/triggers/{sid}/events", params={"limit": 200})
        return [e for e in (r.json() if r.status_code == 200 else [])
                if e.get("goal_id") and e.get("goal_created")]

    goals = wait_until(fired, timeout=300, interval=5, desc="one goal per unique event",
                       done=lambda g: len(g) >= len(unique))
    time.sleep(30)  # late duplicates would land now
    goals = fired()
    per_event: dict[str, int] = {}
    for e in goals:
        k = str((e.get("payload") or {}).get("event_id") or e.get("idempotency_key") or
                e.get("event_id"))
        per_event[k] = per_event.get(k, 0) + 1
    evidence.update(published=len(stream), accepted=codes.count(202), goals=len(goals),
                    per_event=per_event, schedule_worker_restarted=bool(restart))
    soft: list[str] = []
    if any(c != 202 for c in codes):
        soft.append(f"publish answers {sorted(set(codes))}")
    if len(goals) != len(unique):
        soft.append(f"{len(goals)} goals for {len(unique)} unique events "
                    f"({len(stream)} deliveries)")
    if len({e.get("goal_id") for e in goals}) != len(goals):
        soft.append("two trigger events share a goal")
    for g in goals:
        with contextlib.suppress(Exception):
            api.post(f"/goals/{g['goal_id']}/cancel")
    assert not soft, "; ".join(soft)


# ── CHAOS-MODEL-OUTAGE ──────────────────────────────────────────────────────


@pytest.mark.scenario("CHAOS-MODEL-OUTAGE")
def test_model_provider_outage_failover_and_recovery(api: LiveAPI, cleanup: Any,
                                                     evidence: dict[str, Any]) -> None:
    admin = op.admin_client(api)
    op.require_registry_admin(admin)
    dead = {**op.MODELS["chat"], "model_id": f"rw-dead-chat-{tag()}", "base_url": UNROUTABLE,
            "display_name": "unreachable chat model (chaos)"}
    goal_text = "Compute 6 multiplied by 7 and reply with just the number. Do not use tools."
    soft: list[str] = []
    with op.RegistrySandbox(admin) as box:
        for m in (op.MODELS["chat"], dead):
            r = box.register(m)
            assert r["_http"] < 300, f"register {m['model_id']} -> {r}"
        order = [op.key(dead), op.key(op.MODELS["chat"])]
        r = box.set_order("text_generation", order)
        assert r["_http"] < 300, f"preference order -> {r}"
        runs: list[dict[str, Any]] = []
        for i in range(3):
            started = time.monotonic()
            body = api.json_ok("POST", "/goals", json={"goal": goal_text})
            gid = str(body.get("goal_id") or body.get("id"))
            cleanup("POST", f"/goals/{gid}/cancel")
            g = op.wait_goal(api, gid, GOAL_TIMEOUT)
            served = op.models_by_role(op.role_trace(api, gid)) if g.get("status") in (
                "complete", "completed") else {}
            runs.append({"goal_id": gid, "status": g.get("status"),
                         "s": round(time.monotonic() - started, 1),
                         "answer": op.goal_text(g)[:60],
                         "models": {k: sorted(v) for k, v in served.items()}})
        evidence["with_dead_model"] = runs
        for run in runs:
            if run["status"] not in ("complete", "completed"):
                soft.append(f"goal {run['goal_id']} ended {run['status']} although a live "
                            "model was next in the order (no failover)")
            elif "42" not in run["answer"]:
                soft.append(f"goal {run['goal_id']} answered {run['answer']!r}")
            used = {m for ms in run["models"].values() for m in ms}
            if dead["model_id"] in used:
                soft.append(f"the unreachable model is recorded as serving {run['goal_id']}")
        if len(runs) == 3 and runs[0]["s"] > 30 and runs[2]["s"] > runs[0]["s"] * 0.8:
            soft.append(f"no breaker effect: goal times {[r['s'] for r in runs]} (later goals "
                        "still pay the dead model's timeout)")
        health = api.get("/models/health")
        evidence["models_health"] = mask(health.json() if health.status_code == 200 else
                                         health.status_code)[:600]
        box.set_order("text_generation", [op.key(op.MODELS["chat"])])
        started = time.monotonic()
        body = api.json_ok("POST", "/goals", json={"goal": goal_text})
        gid = str(body.get("goal_id") or body.get("id"))
        cleanup("POST", f"/goals/{gid}/cancel")
        g = op.wait_goal(api, gid, GOAL_TIMEOUT)
        evidence["recovered"] = {"status": g.get("status"), "s": round(time.monotonic() -
                                                                       started, 1)}
        if g.get("status") not in ("complete", "completed"):
            soft.append(f"after removing the dead entry the goal ended {g.get('status')}")
    evidence["registry_log"] = box.log
    assert not soft, "; ".join(soft)
