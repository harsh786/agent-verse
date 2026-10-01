"""Workflow fixtures/helpers for the real-world HITL scenarios."""

from __future__ import annotations

import os
from typing import Any

from tests.real_world.helpers import LiveAPI, body_of, mask, tag, wait_until

V1 = "/api/v1"
TERMINAL = {"complete", "failed", "cancelled", "timed_out", "rejected"}
GATE_TIMEOUT = float(os.getenv("RW_GATE_TIMEOUT", "300"))
FINISH_TIMEOUT = float(os.getenv("RW_FINISH_TIMEOUT", "240"))


def weekly_report_yaml(name: str, *, schedule_cron: str | None = None) -> str:
    """A realistic 'draft weekly report -> human approval -> publish summary' workflow.

    draft_report (llm) writes the report as JSON, manager_approval (hitl) gates it,
    publish_summary (code) turns the approved draft into the published summary.
    """
    trigger = (
        f"trigger:\n  type: schedule\n  schedule:\n    cron: \"{schedule_cron}\"\n"
        "    timezone: UTC\n"
        if schedule_cron
        else "trigger:\n  type: api\n"
    )
    return f"""\
name: {name}
description: Draft the weekly engineering report, get manager sign-off, then publish a summary.
tags: [real-world, weekly-report, hitl]
{trigger}inputs:
  team:
    type: string
    required: false
    default: Payments Platform
  week:
    type: string
    required: false
    default: "2026-W40"
  highlights:
    type: string
    required: false
    default: >-
      Shipped UPI autopay retries (checkout success +2.1%); closed 14 of 17 sprint
      tickets; one Sev-2 incident (ledger lag, 38 minutes, fixed by index rebuild).
steps:
  - id: draft_report
    name: Draft weekly report
    type: llm
    json_output: true
    temperature: 0.2
    max_tokens: 700
    timeout: 180s
    on_failure: abort
    prompt: >-
      You are writing the weekly status report for the {{{{inputs.team}}}} team for
      week {{{{inputs.week}}}}. Facts: {{{{inputs.highlights}}}}.
      Return ONLY a JSON object with keys "title" (short string mentioning the team),
      "summary" (2-3 sentences using the facts) and "risks" (array of strings).
  - id: manager_approval
    name: Engineering manager sign-off
    type: hitl
    depends_on: [draft_report]
    timeout: 24h
    timeout_action: escalate
    context:
      - label: Report title
        value: "{{{{steps.draft_report.output.title}}}}"
        display_type: text
      - label: Draft
        value: "{{{{steps.draft_report.output}}}}"
        display_type: json
    actions:
      - id: approve
        label: Publish
        style: success
      - id: reject
        label: Send back
        style: danger
        requires_note: true
  - id: publish_summary
    name: Publish summary
    type: code
    runtime: python
    depends_on: [manager_approval]
    on_failure: abort
    input:
      title: "{{{{steps.draft_report.output.title}}}}"
      summary: "{{{{steps.draft_report.output.summary}}}}"
      decision: "{{{{steps.manager_approval.output.action}}}}"
    code: |
      title = str(inputs.get("title") or "")
      summary = str(inputs.get("summary") or "")
      output = {{
          "published": True,
          "channel": "#eng-weekly",
          "headline": title[:120],
          "summary_words": len(summary.split()),
          "decision": inputs.get("decision"),
      }}
"""


def create_workflow(api: LiveAPI, cleanup: Any, *, schedule_cron: str | None = None,
                    prefix: str = "rw-weekly-report") -> dict[str, Any]:
    name = f"{prefix}-{tag()}"
    resp = api.post(
        f"{V1}/workflows/import-yaml",
        content=weekly_report_yaml(name, schedule_cron=schedule_cron).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code in (200, 201), (
        f"import-yaml -> {resp.status_code}: {mask(resp.text[:500])}"
    )
    wf = resp.json()
    wf_id = str(wf.get("id") or wf.get("workflow_id"))
    cleanup("DELETE", f"{V1}/workflows/{wf_id}")
    return {"id": wf_id, "name": name, "raw": wf}


def trigger(api: LiveAPI, wf_id: str) -> str:
    body = api.json_ok("POST", f"{V1}/workflows/{wf_id}/trigger", json={"inputs": {}})
    return str(body["run_id"])


def get_run(api: LiveAPI, run_id: str) -> dict[str, Any]:
    return dict(api.json_ok("GET", f"{V1}/runs/{run_id}"))


def get_steps(api: LiveAPI, run_id: str) -> dict[str, dict[str, Any]]:
    body = api.json_ok("GET", f"{V1}/runs/{run_id}/steps")
    items = body.get("steps", body.get("items", body)) if isinstance(body, dict) else body
    return {str(s.get("step_id")): s for s in (items or [])}


def step_output(step: dict[str, Any] | None) -> Any:
    if not step:
        return None
    for key in ("output", "outputs", "result", "output_data"):
        if step.get(key) not in (None, "", {}):
            return step[key]
    return None


def wait_status(api: LiveAPI, run_id: str, statuses: set[str], timeout: float) -> dict[str, Any]:
    """Wait until the run reaches one of ``statuses`` (or any terminal status)."""
    return dict(
        wait_until(
            lambda: get_run(api, run_id),
            timeout=timeout,
            interval=3,
            desc=f"run {run_id} to reach {sorted(statuses)}",
            done=lambda r: str(r.get("status")) in statuses | TERMINAL,
        )
    )


def find_approval(api: LiveAPI, run_id: str, timeout: float = 60) -> dict[str, Any]:
    def probe() -> dict[str, Any] | None:
        body = api.json_ok("GET", f"{V1}/approvals", params={"per_page": 100})
        return next((i for i in body.get("items", []) if i.get("run_id") == run_id), None)

    return dict(wait_until(probe, timeout=timeout, interval=3,
                           desc=f"approval for run {run_id} in GET /approvals"))


def decide(api: LiveAPI, request_id: str, action: str, note: str) -> Any:
    resp = api.post(f"{V1}/approvals/{request_id}/decide", json={"action": action, "note": note})
    assert resp.status_code == 200, f"decide {action} -> {resp.status_code}: {mask(resp.text[:500])}"
    return body_of(resp)


def audit_rows(api: LiveAPI, subject_id: str) -> list[dict[str, Any]]:
    """Audit rows whose goal_id is ``subject_id`` (a workflow id or a run id)."""
    resp = api.get("/governance/audit", params={"goal_id": subject_id, "limit": 200})
    if resp.status_code != 200:
        return [{"error": f"/governance/audit -> {resp.status_code}"}]
    return list(resp.json())
