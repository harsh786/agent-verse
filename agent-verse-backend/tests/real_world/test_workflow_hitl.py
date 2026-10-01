"""WF-HITL-*: a realistic approval-gated workflow on the live stack.

draft_report (LLM) -> manager_approval (HITL gate) -> publish_summary (code).
Asserts real outcomes: the run parks at the gate, the approval is visible in the
inbox (list + SSE stream) with the draft as context, the decision routes the run,
every step's output is correct, and the audit trail covers run + decision.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, SSECollector, mask, same_id

INPUTS = {
    "team": "Payments Platform",
    "week": "2026-W40",
    "highlights": (
        "Shipped UPI autopay retries (checkout success +2.1%); closed 14 of 17 sprint "
        "tickets; one Sev-2 incident (ledger lag, 38 minutes, fixed by index rebuild)."
    ),
}


def _trigger(api: LiveAPI, wf_id: str, inputs: dict[str, Any] | None = None) -> str:
    body = api.json_ok(
        "POST", f"{wfx.V1}/workflows/{wf_id}/trigger",
        json={"inputs": INPUTS if inputs is None else inputs},
    )
    return str(body["run_id"])


def _park_at_gate(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                  inputs: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    wf = wfx.create_workflow(api, cleanup)
    evidence["workflow_id"] = wf["id"]
    run_id = _trigger(api, wf["id"], inputs)
    evidence["run_id"] = run_id
    cleanup("POST", f"{wfx.V1}/runs/{run_id}/cancel")  # no-op once terminal
    run = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    evidence["status_at_gate"] = run.get("status")
    assert run.get("status") == "waiting_hitl", (
        f"run did not park at the approval gate: status={run.get('status')} "
        f"error={mask(run.get('error'))}"
    )
    return wf, run_id


def _assert_gate_state(api: LiveAPI, run_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    steps = wfx.get_steps(api, run_id)
    evidence["steps_at_gate"] = {k: v.get("status") for k, v in steps.items()}
    draft = wfx.step_output(steps.get("draft_report"))
    assert steps.get("draft_report", {}).get("status") == "complete", steps.get("draft_report")
    assert isinstance(draft, dict) and str(draft.get("title", "")).strip(), (
        f"LLM draft has no title: {mask(draft)[:300]}"
    )
    assert str(draft.get("summary", "")).strip(), f"LLM draft has no summary: {mask(draft)[:300]}"
    evidence["draft_title"] = str(draft.get("title"))[:120]
    assert steps.get("manager_approval", {}).get("status") == "waiting_hitl", steps.get(
        "manager_approval"
    )
    assert "publish_summary" not in steps or steps["publish_summary"].get("status") in (
        "pending", None
    ), f"publish ran before approval: {steps.get('publish_summary')}"
    return draft


@pytest.mark.scenario("WF-HITL-APPROVE")
def test_wf_hitl_approve(api: LiveAPI, api_key: str, tenant_id: str, cleanup: Any,
                         evidence: dict[str, Any]) -> None:
    soft: list[str] = []
    stream = SSECollector(api_key, f"{wfx.V1}/approvals/stream", max_seconds=150).start()
    try:
        _wf, run_id = _park_at_gate(api, cleanup, evidence)
        draft = _assert_gate_state(api, run_id, evidence)

        approval = wfx.find_approval(api, run_id)
        request_id = str(approval["request_id"])
        evidence["approval_request_id"] = request_id
        assert same_id(approval.get("tenant_id"), tenant_id), approval.get("tenant_id")
        assert approval.get("step_id") == "manager_approval"
        assert approval.get("status") == "pending"
        ctx = {c.get("label"): c.get("value") for c in approval.get("context") or []}
        assert ctx.get("Report title") == draft.get("title"), (
            f"approval context does not carry the draft title: {mask(ctx)[:300]}"
        )
        if not approval.get("workflow_id"):
            soft.append("approval.workflow_id is empty (inbox cannot link the approval to its "
                        "workflow)")

        # The live inbox stream must announce this approval (it polls every 5 s).
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not stream.saw(request_id):
            time.sleep(1)
        evidence["sse_status"] = stream.status_code
        evidence["sse_saw_request"] = stream.saw(request_id)
        if not stream.saw(request_id):
            soft.append(
                f"GET {wfx.V1}/approvals/stream did not announce the approval "
                f"(status={stream.status_code} body={stream.first_body!r} "
                f"error={stream.error} events={len(stream.events)})"
            )
    finally:
        stream.stop()

    decided = wfx.decide(api, request_id, "approve", "Numbers verified - publish it.")
    evidence["decide_status"] = decided.get("status") if isinstance(decided, dict) else decided

    run = wfx.wait_status(api, run_id, {"complete"}, wfx.FINISH_TIMEOUT)
    evidence["final_status"] = run.get("status")
    assert run.get("status") == "complete", f"run ended {run.get('status')}: {mask(run.get('error'))}"

    steps = wfx.get_steps(api, run_id)
    evidence["final_steps"] = {k: v.get("status") for k, v in steps.items()}
    for sid in ("draft_report", "manager_approval", "publish_summary"):
        assert steps.get(sid, {}).get("status") == "complete", f"{sid}: {steps.get(sid)}"
    gate = wfx.step_output(steps["manager_approval"])
    assert gate.get("action") == "approve" and "verified" in str(gate.get("note")), gate
    published = wfx.step_output(steps["publish_summary"])
    evidence["published"] = published
    assert published.get("published") is True
    assert published.get("headline") == str(draft.get("title"))[:120]
    assert published.get("decision") == "approve"
    assert int(published.get("summary_words") or 0) >= 5

    after = api.json_ok("GET", f"{wfx.V1}/approvals/{request_id}")
    evidence["approval_after"] = {k: after.get(k) for k in ("status", "action_taken",
                                                             "reviewed_by")}
    assert after.get("status") in ("approved", "decided", "complete"), after.get("status")
    pending_ids = {
        i.get("request_id")
        for i in api.json_ok("GET", f"{wfx.V1}/approvals", params={"per_page": 100})["items"]
    }
    assert request_id not in pending_ids, "decided approval still listed as pending"

    rows = wfx.run_audit_rows(api, run_id)
    names = sorted({str(r.get("tool_name")) for r in rows})
    evidence["audit_tool_names"] = names
    if not any("run_started" in n or "workflow.run" in n for n in names):
        soft.append(f"no workflow run audit row for run {run_id} (GET /governance/audit "
                    f"goal_id=run_id -> {len(rows)} rows)")
    if not any("hitl" in n or "approval" in n or "decid" in n for n in names):
        soft.append("no approval-decision audit row for the run")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WF-HITL-REJECT")
def test_wf_hitl_reject(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    _wf, run_id = _park_at_gate(api, cleanup, evidence)
    _assert_gate_state(api, run_id, evidence)
    approval = wfx.find_approval(api, run_id)
    request_id = str(approval["request_id"])
    evidence["approval_request_id"] = request_id
    reason = "Sev-2 root cause is not confirmed yet - hold the report."
    wfx.decide(api, request_id, "reject", reason)

    run = wfx.wait_status(api, run_id, {"failed", "cancelled", "rejected"}, wfx.FINISH_TIMEOUT)
    evidence["final_status"] = run.get("status")
    evidence["final_error"] = run.get("error")
    assert run.get("status") in ("failed", "cancelled", "rejected"), run.get("status")
    assert "reject" in str(run.get("error") or "").lower(), (
        f"run error does not say it was rejected: {mask(run.get('error'))}"
    )
    assert run.get("error_step_id") in ("manager_approval", None), run.get("error_step_id")

    # Give a (buggy) engine a moment to run past the gate before checking.
    time.sleep(5)
    steps = wfx.get_steps(api, run_id)
    evidence["final_steps"] = {k: v.get("status") for k, v in steps.items()}
    gate = wfx.step_output(steps.get("manager_approval"))
    assert isinstance(gate, dict) and gate.get("action") == "reject", gate
    assert gate.get("note") == reason, f"rejection note lost: {mask(gate)}"
    publish = steps.get("publish_summary")
    assert publish is None or publish.get("status") in ("pending", "skipped", None), (
        f"publish_summary ran after a rejection: {mask(publish)}"
    )
    after = api.json_ok("GET", f"{wfx.V1}/approvals/{request_id}")
    evidence["approval_after"] = {k: after.get(k) for k in ("status", "action_taken")}
    assert after.get("status") in ("rejected", "decided"), after.get("status")


@pytest.mark.scenario("WF-HITL-RESTART")
@pytest.mark.skipif(os.getenv("RW_HITL_PERSIST", "1") == "0", reason="RW_HITL_PERSIST=0")
def test_wf_hitl_paused_state_persists(api: LiveAPI, cleanup: Any,
                                       evidence: dict[str, Any]) -> None:
    """While paused the run stays parked and resumable (Postgres-backed), then completes.

    Containers are NOT restarted: persistence is verified through fresh API reads
    after a delay (each read is served from Postgres, not a worker's memory).
    """
    _wf, run_id = _park_at_gate(api, cleanup, evidence)
    approval = wfx.find_approval(api, run_id)
    request_id = str(approval["request_id"])
    evidence["approval_request_id"] = request_id

    delay = float(os.getenv("RW_PERSIST_DELAY", "45"))
    time.sleep(delay)
    run = wfx.get_run(api, run_id)
    evidence["status_after_delay"] = run.get("status")
    assert run.get("status") == "waiting_hitl", f"paused run drifted to {run.get('status')}"
    listed = api.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": _wf["id"],
                                                         "per_page": 20})
    items = listed.get("items", []) if isinstance(listed, dict) else listed
    mine = [r for r in items if str(r.get("run_id") or r.get("id")) == run_id]
    evidence["listed_status"] = mine[0].get("status") if mine else None
    assert mine and mine[0].get("status") == "waiting_hitl", "run not listed as waiting_hitl"
    still = api.json_ok("GET", f"{wfx.V1}/approvals/{request_id}")
    assert still.get("status") == "pending", still.get("status")

    wfx.decide(api, request_id, "approve", f"Approved after {delay:.0f}s pause.")
    run = wfx.wait_status(api, run_id, {"complete"}, wfx.FINISH_TIMEOUT)
    evidence["final_status"] = run.get("status")
    assert run.get("status") == "complete", mask(run.get("error"))
    steps = wfx.get_steps(api, run_id)
    assert (wfx.step_output(steps.get("publish_summary")) or {}).get("published") is True


@pytest.mark.scenario("WF-HITL-CANCEL")
def test_wf_cancel_while_paused_withdraws_approval(api: LiveAPI, cleanup: Any,
                                                   evidence: dict[str, Any]) -> None:
    """Cancelling a run parked at the gate must withdraw its approval; a late
    decision must not resurrect the cancelled run or run later steps."""
    _wf, run_id = _park_at_gate(api, cleanup, evidence)
    request_id = str(wfx.find_approval(api, run_id)["request_id"])
    evidence["approval_request_id"] = request_id
    resp = api.post(f"{wfx.V1}/runs/{run_id}/cancel")
    evidence["cancel_http"] = resp.status_code
    assert resp.status_code in (200, 202), f"cancel -> {resp.status_code}: {mask(resp.text[:300])}"
    wfx.wait_status(api, run_id, {"cancelled"}, 60)
    time.sleep(3)
    appr = api.json_ok("GET", f"{wfx.V1}/approvals/{request_id}")
    evidence["approval_after_cancel"] = appr.get("status")
    soft = []
    if appr.get("status") == "pending":
        soft.append("approval stays pending in the inbox after its run was cancelled")
        # A reviewer acting on that stale card:
        late = api.post(f"{wfx.V1}/approvals/{request_id}/decide",
                        json={"action": "approve", "note": "late approval"})
        evidence["late_decide_http"] = late.status_code
        time.sleep(8)
    run = wfx.get_run(api, run_id)
    steps = wfx.get_steps(api, run_id)
    evidence["final_status"] = run.get("status")
    evidence["final_steps"] = {k: v.get("status") for k, v in steps.items()}
    if run.get("status") != "cancelled":
        soft.append(f"cancelled run changed to {run.get('status')!r} after a late decision")
    if (steps.get("publish_summary") or {}).get("status") == "complete":
        soft.append("publish_summary RAN on a cancelled run after a late approval")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("WF-INPUT-DEFAULTS")
def test_wf_input_defaults_applied(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    """A run started with no inputs (schedule/webhook style) must get the declared defaults."""
    wf = wfx.create_workflow(api, cleanup)
    run_id = _trigger(api, wf["id"], inputs={})
    evidence["run_id"] = run_id
    cleanup("POST", f"{wfx.V1}/runs/{run_id}/cancel")
    wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    steps = wfx.get_steps(api, run_id)
    prompt = str((steps.get("draft_report", {}).get("input") or {}).get("prompt", ""))
    evidence["resolved_prompt_head"] = prompt[:160]
    assert "Payments Platform" in prompt and "2026-W40" in prompt, (
        f"declared input defaults were not applied to the run: prompt={prompt[:200]!r}"
    )


@pytest.mark.scenario("WF-APPROVALS-TENANT-ISOLATION")
def test_workflow_approvals_are_tenant_scoped(api: LiveAPI, tenant_id: str,
                                              evidence: dict[str, Any]) -> None:
    """GET /api/v1/approvals (and /stats) must only ever show the caller's tenant."""
    body = api.json_ok("GET", f"{wfx.V1}/approvals", params={"per_page": 100})
    foreign = [i for i in body.get("items", []) if not same_id(i.get("tenant_id"), tenant_id)]
    evidence["listed"] = len(body.get("items", []))
    evidence["foreign_items"] = len(foreign)
    evidence["foreign_tenants"] = sorted({str(i.get("tenant_id"))[:8] + "..." for i in foreign})
    evidence["foreign_workflows"] = sorted({str(i.get("workflow_name")) for i in foreign})[:5]
    assert not foreign, (
        f"{len(foreign)} approval(s) of OTHER tenants visible in GET /api/v1/approvals "
        f"(tenants {evidence['foreign_tenants']})"
    )
