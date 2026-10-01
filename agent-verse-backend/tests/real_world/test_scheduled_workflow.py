"""SCHEDULED-WF-HITL: an unattended (scheduled) workflow that parks at an approval gate.

The workflow's own schedule trigger is activated by publishing it and fired by
the beat's ``workflow.fire_due_workflow_schedules`` scan. The cron is the
tenant plan's floor (``GET /schedules/plan-floors``) rounded to whole minutes,
capped by ``RW_SCHEDULE_MAX_WAIT``; when the floor is longer than that, or the
publish is refused, the scenario falls back to the workflow's signed webhook —
still an unattended trigger. Asserts the run starts as such, pauses for
approval and completes on approval; the schedule is removed afterwards.

WF-SCHEDULE-PLAN-FLOOR checks that a workflow cron below the plan floor is not
accepted (refused at creation, and at publish).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.real_world import workflows as wfx
from tests.real_world.helpers import BASE_URL, LiveAPI, body_of, mask, wait_until

MAX_WAIT = float(os.getenv("RW_SCHEDULE_MAX_WAIT", "960"))


def _plan_floor(api: LiveAPI) -> tuple[str, int | None]:
    plan = str(api.json_ok("GET", "/tenants/me").get("plan") or "free")
    resp = api.get("/schedules/plan-floors")
    if resp.status_code != 200:
        return plan, None
    floors = resp.json().get("plan_min_interval_seconds") or {}
    return plan, int(floors.get(plan) or floors.get("free") or 0) or None


def _cron_every(seconds: int) -> str:
    minutes = max(1, -(-seconds // 60))
    return "* * * * *" if minutes == 1 else f"*/{minutes} * * * *"


def _first_run(api: LiveAPI, workflow_id: str) -> dict[str, Any] | None:
    body = api.json_ok("GET", f"{wfx.V1}/runs", params={"workflow_id": workflow_id,
                                                        "per_page": 10})
    items = body.get("items", []) if isinstance(body, dict) else body
    return items[-1] if items else None


@pytest.mark.scenario("SCHEDULED-WF-HITL")
def test_scheduled_workflow_with_approval_gate(api: LiveAPI, cleanup: Any,
                                               evidence: dict[str, Any]) -> None:
    plan, floor = _plan_floor(api)
    evidence["plan"], evidence["plan_floor_s"] = plan, floor
    cron = _cron_every(floor or 60)
    run_id = ""
    if (floor or 60) <= MAX_WAIT:
        wf = wfx.create_workflow(api, cleanup, schedule_cron=cron, prefix="rw-sched-report")
        evidence["workflow_id"], evidence["cron"] = wf["id"], cron
        cleanup("POST", f"{wfx.V1}/workflows/{wf['id']}/unpublish")
        pub = api.post(f"{wfx.V1}/workflows/{wf['id']}/publish")
        evidence["publish_http"] = pub.status_code
        if pub.status_code == 200:
            run = wait_until(lambda: _first_run(api, wf["id"]), timeout=(floor or 60) + 150,
                             interval=5, desc=f"the cron schedule {cron!r} to fire a run")
            run_id = str(run.get("run_id") or run.get("id"))
            api.post(f"{wfx.V1}/workflows/{wf['id']}/unpublish")  # stop further firings
            expected_trigger = "schedule"
        else:
            evidence["publish_refused"] = mask(pub.text[:200])
    if not run_id:  # plan floor too long to wait for, or cron refused: webhook trigger
        wf = wfx.create_workflow(api, cleanup, prefix="rw-hook-report")
        evidence["workflow_id"], evidence["mode"] = wf["id"], "webhook-fallback"
        cleanup("POST", f"{wfx.V1}/workflows/{wf['id']}/unpublish")
        pub = api.json_ok("POST", f"{wfx.V1}/workflows/{wf['id']}/publish")
        path = str(pub.get("webhook_path") or "")
        assert path.startswith("/wf-hooks/"), f"publish returned no webhook path: {mask(pub)}"
        import httpx

        hook = httpx.post(f"{BASE_URL}{path}", json={"team": "Payments Platform"}, timeout=60)
        evidence["webhook_http"] = hook.status_code
        assert hook.status_code in (200, 202), f"webhook -> {hook.status_code}: " \
            f"{mask(hook.text[:200])}"
        run_id = str(body_of(hook).get("run_id"))
        expected_trigger = "webhook"
    evidence["run_id"] = run_id
    cleanup("POST", f"{wfx.V1}/runs/{run_id}/cancel")

    debug = api.get(f"{wfx.V1}/runs/{run_id}/debug")
    trig = (debug.json().get("run") or {}).get("trigger_type") if debug.status_code == 200 \
        else None
    evidence["trigger_type"] = trig
    assert trig == expected_trigger, f"run trigger_type={trig}, expected {expected_trigger}"

    parked = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    evidence["status_at_gate"] = parked.get("status")
    assert parked.get("status") == "waiting_hitl", (
        f"unattended run did not pause for approval: {parked.get('status')} "
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


@pytest.mark.scenario("WF-SCHEDULE-PLAN-FLOOR")
def test_workflow_cron_respects_plan_floor(api: LiveAPI, cleanup: Any,
                                           evidence: dict[str, Any]) -> None:
    plan, floor = _plan_floor(api)
    evidence["plan"], evidence["plan_floor_s"] = plan, floor
    if not floor or floor <= 60:
        pytest.skip(f"plan {plan} allows every-minute schedules (floor {floor})")
    # Refused at creation (422) since WF-PLAN-FLOOR; publish is checked too.
    created = api.post(
        f"{wfx.V1}/workflows/import-yaml",
        content=wfx.weekly_report_yaml(
            f"rw-floor-check-{wfx.tag()}", schedule_cron="* * * * *"
        ).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    evidence["create_http"] = created.status_code
    if created.status_code == 422:
        evidence["create_detail"] = mask(created.text[:200])
        return
    assert created.status_code in (200, 201), mask(created.text[:300])
    wf = {"id": str(created.json().get("id") or created.json().get("workflow_id"))}
    cleanup("DELETE", f"{wfx.V1}/workflows/{wf['id']}")
    evidence["workflow_id"] = wf["id"]
    cleanup("POST", f"{wfx.V1}/workflows/{wf['id']}/unpublish")
    pub = api.post(f"{wfx.V1}/workflows/{wf['id']}/publish")
    evidence["publish_http"] = pub.status_code
    evidence["publish_detail"] = mask(pub.text[:200])
    if pub.status_code == 200:
        api.post(f"{wfx.V1}/workflows/{wf['id']}/unpublish")
    assert pub.status_code in (409, 422), (
        f"a 1-minute workflow cron was published on plan {plan!r} whose schedule floor is "
        f"{floor}s"
    )
