"""WF-COMPLEX-PIPELINE, WF-FAILURE-RECOVERY and WF-CANCEL-AND-APPROVAL.

WF-COMPLEX-PIPELINE (12 steps + 3 parallel branches): HTTP fetch from the local
fixture server → code parse → conditional branch → 3-way parallel fan-out + join →
LLM summary → a deliberately flaky ledger post with retries → human approval gate →
compose (variables carried across the gate) → publish. Asserts every step's output
against ground truth computed from the fixture data, the retry count *as seen by
the fixture server*, the variable after resume, the published payload, cost
recorded, audit rows for run + steps, and that the run view is self-consistent.

WF-FAILURE-RECOVERY: a permanent step failure fails the run with the reason and the
failing step; rerun-from-failed (POST /runs/{id}/retry) completes WITHOUT re-running
completed steps (side-effect counters on the fixture server stay at 1).

WF-CANCEL-AND-APPROVAL: cancelling a run parked at a gate resolves/expires its
approval; a late approval is refused with 409 and never resurrects the run.

The workflow HTTP step's SSRF guard refuses private addresses, so the stack must
reach the fixture server through RW_FIXTURE_PUBLIC_URL (a tunnel) — otherwise the
HTTP scenarios are SKIPPED with that reason after the guard's refusal is observed.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.real_world import wf_complex as wfc
from tests.real_world import workflows as wfx
from tests.real_world.fixture_server import FixtureServer
from tests.real_world.helpers import LiveAPI, mask, same_id, tag
from tests.real_world.metrics import record


import_yaml = wfx.import_yaml
trigger = wfx.trigger_run
out = wfx.output_of


def skip_if_ssrf_blocked(run: dict[str, Any], steps: dict[str, dict[str, Any]]) -> None:
    errors = " ".join(str(s.get("error") or "") for s in steps.values()) + str(
        run.get("error") or "")
    if "ssrf" in errors.lower() and not FixtureServer.is_public():
        pytest.skip("the workflow HTTP step's SSRF guard refused the local fixture server "
                    f"({mask(errors)[:160]}); set RW_FIXTURE_PUBLIC_URL to a tunnel to "
                    "RW_FIXTURE_PORT so the stack can reach it")


@pytest.mark.scenario("WF-COMPLEX-PIPELINE")
def test_wf_complex_pipeline(api: LiveAPI, cleanup: Any, fixture_server: FixtureServer,
                             evidence: dict[str, Any]) -> None:
    key = tag()
    base = fixture_server.public_base
    expect = wfc.expected_pipeline()
    wf_id = import_yaml(api, cleanup, wfc.complex_pipeline_yaml(f"rw-order-release-{key}",
                                                               base, key))
    run_id = trigger(api, cleanup, wf_id)
    evidence.update(workflow_id=wf_id, run_id=run_id, fixture_base=base)
    started = time.monotonic()
    run = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    to_gate_s = time.monotonic() - started
    steps = wfx.get_steps(api, run_id)
    skip_if_ssrf_blocked(run, steps)
    evidence["status_at_gate"] = run.get("status")
    evidence["steps_at_gate"] = {k: v.get("status") for k, v in steps.items()}
    assert run.get("status") == "waiting_hitl", (
        f"run did not reach the approval gate: {run.get('status')} at "
        f"{run.get('error_step_id')}: {mask(run.get('error'))}"
    )
    soft: list[str] = []

    # Before the gate: data steps against ground truth.
    assert len(out(steps, "fetch_orders").get("orders", [])) == expect["count"]
    parsed = out(steps, "parse_orders")
    evidence["parsed"] = parsed
    for k in ("count", "gross", "high_value", "cold_chain", "batch"):
        assert parsed.get(k) == expect[k], f"parse_orders.{k}={parsed.get(k)!r} != {expect[k]!r}"
    assert out(steps, "route_lane").get("chosen_branch") == f"lane_{expect['lane']}", \
        out(steps, "route_lane")
    assert steps.get(f"lane_{expect['lane']}", {}).get("status") == "complete"
    other = "lane_standard" if expect["lane"] == "expedite" else "lane_expedite"
    if steps.get(other, {}).get("status") == "complete":
        soft.append(f"the branch not taken ({other}) ran as well")
    fan = out(steps, "enrich")
    evidence["enrich_keys"] = sorted(fan) if isinstance(fan, dict) else fan
    assert isinstance(fan, dict) and {"fx_rates", "inventory", "carrier_sla"} <= set(fan), fan
    assert not any(isinstance(v, dict) and v.get("_error") for v in fan.values()), fan
    for path in (f"/fx-rates/{key}", f"/inventory/{key}", f"/carrier-sla/{key}"):
        assert fixture_server.count("GET", path) == 1, f"{path} called " \
            f"{fixture_server.count('GET', path)} times (want exactly 1)"
    joined = out(steps, "join_context")
    evidence["joined"] = joined
    for k in ("lane", "gross_usd", "coldbox_stock", "best_carrier"):
        assert joined.get(k) == expect[k], f"join_context.{k}={joined.get(k)!r} != {expect[k]!r}"
    summary = out(steps, "summarise")
    evidence["summary"] = summary
    assert str(summary.get("headline", "")).strip(), f"LLM summary has no headline: {summary}"
    if summary.get("risk_level") not in ("low", "medium", "high"):
        soft.append(f"LLM risk_level not in the allowed set: {summary.get('risk_level')!r}")
    ledger = out(steps, "post_ledger")
    ledger_calls = fixture_server.count("POST", f"/flaky/{key}")
    evidence["ledger"] = {"output": ledger, "calls_seen_by_server": ledger_calls}
    record(evidence, ledger_attempts=ledger_calls, seconds_to_gate=round(to_gate_s, 1))
    assert ledger_calls == 3, f"flaky ledger called {ledger_calls} times (2 failures + 1 success)"
    assert ledger.get("attempt") == 3 and ledger.get("ok") is True, ledger
    assert fixture_server.published.get(key) is None, "published before the approval"

    # The gate: approval carries the variable computed before it.
    approval = wfx.find_approval(api, run_id)
    request_id = str(approval["request_id"])
    evidence["approval_request_id"] = request_id
    ctx = {c.get("label"): c.get("value") for c in approval.get("context") or []}
    evidence["approval_context"] = ctx
    assert ctx.get("Lane") == expect["lane"], f"approval context lane: {ctx}"
    assert ctx.get("Batch") == expect["batch"], f"approval context batch: {ctx}"
    wfx.decide(api, request_id, "approve", "Totals reconciled with the ledger - release.")

    run = wfx.wait_status(api, run_id, {"complete"}, wfx.FINISH_TIMEOUT)
    evidence["final_status"] = run.get("status")
    assert run.get("status") == "complete", f"{run.get('status')} at {run.get('error_step_id')}" \
        f": {mask(run.get('error'))}"
    steps = wfx.get_steps(api, run_id)
    evidence["final_steps"] = {k: v.get("status") for k, v in steps.items()}
    composed = out(steps, "compose_release")
    evidence["composed"] = composed
    assert composed.get("lane") == expect["lane"], (
        f"vars.lane after resume is {composed.get('lane')!r}, set to {expect['lane']!r} "
        "before the gate (workflow variables lost across the approval)"
    )
    assert composed.get("gross") == expect["gross"] and composed.get("batch") == expect["batch"]
    assert composed.get("decision") == "approve" and "reconciled" in str(
        composed.get("approver_note")), composed
    assert composed.get("ledger_attempt") == 3, composed
    published = fixture_server.published.get(key) or []
    evidence["published"] = published
    assert len(published) == 1, f"publish called {len(published)} times"
    assert published[0].get("lane") == expect["lane"] and published[0].get("batch") == \
        expect["batch"], published[0]
    for sid in wfc.PIPELINE_TOP_LEVEL:
        if sid == other:
            continue
        if steps.get(sid, {}).get("status") != "complete":
            soft.append(f"step {sid} is {steps.get(sid, {}).get('status')!r}, not complete")
    for sid, path in (("fetch_orders", f"/orders/{key}.json"),):
        if fixture_server.count("GET", path) != 1:
            soft.append(f"{sid} re-ran after the gate ({fixture_server.count('GET', path)} calls)")

    # Cost + run view consistency.
    record(evidence, cost_usd=float(run.get("cost_usd") or 0),
           tokens_used=int(run.get("tokens_used") or 0),
           duration_ms=float(run.get("duration_ms") or 0))
    if not (float(run.get("cost_usd") or 0) > 0 or int(run.get("tokens_used") or 0) > 0):
        soft.append("the run records no LLM cost or tokens although the summarise step ran")
    listed = api.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": wf_id, "per_page": 10})
    items = listed.get("items", []) if isinstance(listed, dict) else listed
    mine = next((r for r in items if same_id(r.get("run_id"), run_id)), None)
    if not mine or mine.get("status") != "complete":
        soft.append(f"run list shows {mine and mine.get('status')} for the completed run")
    if int(run.get("step_count") or 0) not in (0, len(steps)):
        soft.append(f"run.step_count={run.get('step_count')} but /steps lists {len(steps)}")
    debug = api.get(f"{wfx.V1}/runs/{run_id}/debug")
    if debug.status_code == 200:
        dstatus = (debug.json().get("run") or {}).get("status")
        if dstatus and dstatus != "complete":
            soft.append(f"debug view says {dstatus!r}")

    # Audit: run + approval + per-step lifecycle rows.
    rows = wfx.audit_rows(api, wf_id) + wfx.audit_rows(api, run_id)
    names = [str(r.get("tool_name")) for r in rows]
    evidence["audit_counts"] = {n: names.count(n) for n in sorted(set(names))}
    if not any(n == "workflow.run_triggered" for n in names):
        soft.append("no workflow.run_triggered audit row")
    if not any("approval_decided" in n or "hitl.decided" in n for n in names):
        soft.append("no audit row for the approval decision")
    step_rows = [n for n in names if "step.completed" in n or "step_completed" in n]
    if len(step_rows) < 8:
        soft.append(f"only {len(step_rows)} step-completed audit rows for 11 completed steps")
    if not any(n.endswith("completed") and "step" not in n for n in names):
        soft.append("no run-completed audit row")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WF-FAILURE-RECOVERY")
def test_wf_failure_and_rerun_from_failed(api: LiveAPI, cleanup: Any,
                                          fixture_server: FixtureServer,
                                          evidence: dict[str, Any]) -> None:
    key = tag()
    fixture_server.set_switch(key, True)  # the finance confirmation endpoint is down
    wf_id = import_yaml(api, cleanup, wfc.recovery_yaml(f"rw-charge-confirm-{key}",
                                                       fixture_server.public_base, key))
    run_id = trigger(api, cleanup, wf_id)
    evidence.update(workflow_id=wf_id, run_id=run_id)
    run = wfx.wait_status(api, run_id, {"failed"}, wfx.FINISH_TIMEOUT)
    steps = wfx.get_steps(api, run_id)
    skip_if_ssrf_blocked(run, steps)
    evidence["failed_run"] = {"status": run.get("status"), "error_step_id":
                              run.get("error_step_id"), "error": mask(run.get("error"))[:200]}
    assert run.get("status") == "failed", f"run ended {run.get('status')}"
    assert run.get("error_step_id") == "confirm", run.get("error_step_id")
    assert "500" in str(run.get("error")) or "finance" in str(run.get("error")).lower(), (
        f"failure reason does not name the cause: {mask(run.get('error'))}"
    )
    assert steps.get("finish", {}).get("status") in (None, "pending", "skipped"), steps.get(
        "finish")
    charge_path, orders_path = f"/charge/{key}", f"/orders/{key}.json"
    assert fixture_server.count("POST", charge_path) == 1
    first_confirm = fixture_server.count("GET", f"/switch/{key}")

    fixture_server.set_switch(key, False)  # finance recovers
    resp = api.post(f"{wfx.V1}/runs/{run_id}/retry")
    evidence["retry_http"] = resp.status_code
    if resp.status_code in (404, 405):
        pytest.fail(f"rerun-from-failed is not supported: POST /runs/{{id}}/retry -> "
                    f"{resp.status_code}")
    assert resp.status_code == 202, f"retry -> {resp.status_code}: {mask(resp.text[:300])}"
    new_id = str(resp.json().get("run_id"))
    cleanup("POST", f"{wfx.V1}/runs/{new_id}/cancel")
    evidence["retry_run_id"] = new_id
    assert new_id and new_id != run_id
    new_run = wfx.wait_status(api, new_id, {"complete"}, wfx.FINISH_TIMEOUT)
    evidence["retry_status"] = new_run.get("status")
    assert new_run.get("status") == "complete", mask(new_run.get("error"))
    counts = {"orders": fixture_server.count("GET", orders_path),
              "charge": fixture_server.count("POST", charge_path),
              "confirm": fixture_server.count("GET", f"/switch/{key}")}
    evidence["side_effect_counts"] = counts
    assert counts["charge"] == 1, f"the completed charge step ran again ({counts['charge']} " \
        "charges): a retry must reuse completed steps"
    assert counts["orders"] == 1, f"the completed fetch step ran again ({counts['orders']})"
    assert counts["confirm"] == first_confirm + 1, counts
    finished = out(wfx.get_steps(api, new_id), "finish")
    evidence["finish_output"] = finished
    assert finished.get("done") is True and finished.get("charge_calls_seen") == 1, finished
    again = api.post(f"{wfx.V1}/runs/{new_id}/retry")
    evidence["retry_of_complete_http"] = again.status_code
    assert again.status_code == 409, f"retrying a completed run -> {again.status_code}"


@pytest.mark.scenario("WF-CANCEL-AND-APPROVAL")
def test_wf_cancel_resolves_approval_and_late_approve_409(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    wf = wfx.create_workflow(api, cleanup, prefix="rw-cancel-gate")
    run_id = trigger(api, cleanup, wf["id"])
    evidence.update(workflow_id=wf["id"], run_id=run_id)
    run = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    assert run.get("status") == "waiting_hitl", mask(run.get("error"))
    request_id = str(wfx.find_approval(api, run_id)["request_id"])
    evidence["approval_request_id"] = request_id
    resp = api.post(f"{wfx.V1}/runs/{run_id}/cancel")
    assert resp.status_code in (200, 202), f"cancel -> {resp.status_code}"
    wfx.wait_status(api, run_id, {"cancelled"}, 60)
    appr = api.json_ok("GET", f"{wfx.V1}/approvals/{request_id}")
    evidence["approval_after_cancel"] = {k: appr.get(k) for k in ("status", "action_taken")}
    assert appr.get("status") not in ("pending", None), (
        "the approval is still pending after its run was cancelled (should be resolved/expired)"
    )
    pending = {i.get("request_id") for i in api.json_ok(
        "GET", f"{wfx.V1}/approvals", params={"per_page": 100}).get("items", [])}
    assert request_id not in pending, "the cancelled run's approval is still in the inbox"
    late = api.post(f"{wfx.V1}/approvals/{request_id}/decide",
                    json={"action": "approve", "note": "late approval"})
    evidence["late_decide_http"] = late.status_code
    assert late.status_code == 409, f"a late approval answered {late.status_code}, not 409: " \
        f"{mask(late.text[:200])}"
    time.sleep(5)
    run = wfx.get_run(api, run_id)
    steps = wfx.get_steps(api, run_id)
    evidence["final"] = {"status": run.get("status"),
                         "steps": {k: v.get("status") for k, v in steps.items()}}
    assert run.get("status") == "cancelled", f"cancelled run became {run.get('status')}"
    assert steps.get("publish_summary", {}).get("status") != "complete", (
        "publish_summary ran on a cancelled run"
    )
