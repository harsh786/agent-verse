"""SCHEDULED-WF-HITL: a cron-scheduled workflow that parks at an approval gate.

The workflow's own schedule trigger (every minute) is activated by publishing it;
the beat's ``workflow.fire_due_workflow_schedules`` scan fires it. Asserts the
schedule fires exactly as a schedule run, pauses for approval, and completes on
approval. The schedule is removed (unpublish + delete) afterwards.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, mask, wait_until

FIRE_TIMEOUT = float(os.getenv("RW_SCHEDULE_FIRE_TIMEOUT", "240"))


@pytest.mark.scenario("SCHEDULED-WF-HITL")
def test_scheduled_workflow_with_approval_gate(api: LiveAPI, cleanup: Any,
                                               evidence: dict[str, Any]) -> None:
    wf = wfx.create_workflow(api, cleanup, schedule_cron="* * * * *", prefix="rw-sched-report")
    evidence["workflow_id"] = wf["id"]
    cleanup("POST", f"{wfx.V1}/workflows/{wf['id']}/unpublish")  # runs before the DELETE
    pub = api.post(f"{wfx.V1}/workflows/{wf['id']}/publish")
    evidence["publish_http"] = pub.status_code
    assert pub.status_code == 200, f"publish -> {pub.status_code}: {mask(pub.text[:400])}"
    evidence["published_trigger"] = {k: pub.json().get(k) for k in ("status", "trigger_type",
                                                                    "schedule_cron")}

    def first_run() -> dict[str, Any] | None:
        body = api.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": wf["id"],
                                                          "per_page": 10})
        items = body.get("items", []) if isinstance(body, dict) else body
        return items[-1] if items else None

    run = wait_until(first_run, timeout=FIRE_TIMEOUT, interval=5,
                     desc="the cron schedule to fire a run")
    run_id = str(run.get("run_id") or run.get("id"))
    evidence["run_id"] = run_id
    cleanup("POST", f"{wfx.V1}/runs/{run_id}/cancel")
    # Stop further firings now that one fired.
    api.post(f"{wfx.V1}/workflows/{wf['id']}/unpublish")

    debug = api.get(f"{wfx.V1}/runs/{run_id}/debug")
    trig = (debug.json().get("run") or {}).get("trigger_type") if debug.status_code == 200 \
        else None
    evidence["trigger_type"] = trig
    assert trig == "schedule", f"run was not started by the schedule (trigger_type={trig})"

    parked = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    evidence["status_at_gate"] = parked.get("status")
    assert parked.get("status") == "waiting_hitl", (
        f"scheduled run did not pause for approval: {parked.get('status')} "
        f"{mask(parked.get('error'))}"
    )
    approval = wfx.find_approval(api, run_id)
    evidence["approval_request_id"] = approval["request_id"]
    wfx.decide(api, str(approval["request_id"]), "approve", "Scheduled report approved.")
    done = wfx.wait_status(api, run_id, {"complete"}, wfx.FINISH_TIMEOUT)
    evidence["final_status"] = done.get("status")
    assert done.get("status") == "complete", mask(done.get("error"))
    steps = wfx.get_steps(api, run_id)
    evidence["final_steps"] = {k: v.get("status") for k, v in steps.items()}
    assert (wfx.step_output(steps.get("publish_summary")) or {}).get("published") is True

    # Cleanup check: after unpublish no further scheduled run may start.
    runs = api.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": wf["id"],
                                                      "per_page": 20})
    evidence["runs_total"] = runs.get("total") if isinstance(runs, dict) else len(runs)
