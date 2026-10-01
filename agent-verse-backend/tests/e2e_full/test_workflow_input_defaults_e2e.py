"""e2e_full (WF-INPUT-DEFAULTS): declared workflow input defaults are applied to
every run — API, webhook and schedule — and a required input with no value is
refused with the reason (422 for API/webhook, a FAILED run for a schedule).

Before: ``WorkflowRunner._validate_inputs`` only used ``InputDefinition.default``
to skip the required check; nothing merged the defaults into the run's inputs,
and the context resolver renders a missing input as ``''`` — so a schedule /
webhook / ``{}`` API run prompted the LLM with "for the  team for week .".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.e2e_full._wf_worker import API, create_workflow

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


_DEFAULTED = {
    "name": "Weekly report",
    "inputs": {
        "team": {"type": "string", "required": True, "default": "platform"},
        "week": {"type": "string", "required": False, "default": "W40"},
    },
    "steps": [
        {
            "id": "render",
            "type": "transform",
            "input": {"team": "{{inputs.team}}", "week": "{{inputs.week}}"},
        }
    ],
}

_REQUIRED = {
    "name": "Needs facts",
    "inputs": {"facts": {"type": "string", "required": True}},
    "steps": [{"id": "render", "type": "transform", "input": {"facts": "{{inputs.facts}}"}}],
}


async def _step_output(client: Any, run_id: str, step_id: str) -> Any:
    resp = await client.get(f"{API}/runs/{run_id}/steps")
    assert resp.status_code == 200, resp.text
    for row in resp.json():
        if row["step_id"] == step_id:
            return row.get("output")
    raise AssertionError(f"no {step_id!r} step in {resp.json()!r}")


async def test_api_run_without_inputs_gets_declared_defaults(tenant_client: Any) -> None:
    wid = await create_workflow(tenant_client, _DEFAULTED, name=f"defaults-{uuid.uuid4().hex[:8]}")

    resp = await tenant_client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {}, "dry_run": False}
    )
    assert resp.status_code == 202, resp.text
    run = (await tenant_client.get(f"{API}/runs/{resp.json()['run_id']}")).json()
    assert run["inputs"] == {"team": "platform", "week": "W40"}

    # A supplied value wins over the default; the other default still applies.
    resp = await tenant_client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {"team": "infra"}, "dry_run": True}
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    assert (await tenant_client.get(f"{API}/runs/{run_id}")).json()["inputs"] == {
        "team": "infra",
        "week": "W40",
    }


async def test_required_input_without_value_is_refused_with_reason(tenant_client: Any) -> None:
    wid = await create_workflow(tenant_client, _REQUIRED, name=f"required-{uuid.uuid4().hex[:8]}")
    resp = await tenant_client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {}, "dry_run": False}
    )
    assert resp.status_code == 422, resp.text
    assert "facts" in resp.text


async def test_webhook_run_gets_defaults_and_missing_required_is_422(
    client: Any, tenant_client: Any
) -> None:
    webhook = {"trigger": {"type": "webhook"}}
    wid = await create_workflow(
        tenant_client, {**_DEFAULTED, **webhook}, name=f"hook-{uuid.uuid4().hex[:8]}"
    )
    pub = await tenant_client.post(f"{API}/workflows/{wid}/publish")
    assert pub.status_code == 200, pub.text
    hook = await client.post(pub.json()["webhook_path"], json={})
    assert hook.status_code in (200, 202), hook.text
    run = (await tenant_client.get(f"{API}/runs/{hook.json()['run_id']}")).json()
    assert run["trigger_type"] == "webhook"
    assert run["inputs"] == {"team": "platform", "week": "W40"}

    req_id = await create_workflow(
        tenant_client, {**_REQUIRED, **webhook}, name=f"hook-req-{uuid.uuid4().hex[:8]}"
    )
    pub = await tenant_client.post(f"{API}/workflows/{req_id}/publish")
    assert pub.status_code == 200, pub.text
    refused = await client.post(pub.json()["webhook_path"], json={})
    assert refused.status_code == 422, refused.text
    assert "facts" in refused.text


async def test_scheduled_runs_get_defaults_or_fail_with_reason(
    app: Any, tenant_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workflow import celery_tasks as ct

    schedule = {"trigger": {"type": "schedule", "schedule": {"cron": "0 9 * * 1"}}}
    wid = await create_workflow(
        tenant_client, {**_DEFAULTED, **schedule}, name=f"sched-{uuid.uuid4().hex[:8]}"
    )
    req_id = await create_workflow(
        tenant_client, {**_REQUIRED, **schedule}, name=f"sched-req-{uuid.uuid4().hex[:8]}"
    )
    for workflow_id in (wid, req_id):
        pub = await tenant_client.post(f"{API}/workflows/{workflow_id}/publish")
        assert pub.status_code == 200, pub.text

    # Make the weekly cron due now (the scan itself, its dedup and runner.run
    # are the real ones).
    now = datetime.now(UTC)
    monkeypatch.setattr(
        ct, "_cron_bounds", lambda *_a, **_k: (now - timedelta(seconds=5), now + timedelta(days=7))
    )
    monkeypatch.setattr(ct, "_get_runner", lambda: app.state.workflow_runner)
    await ct.fire_due_workflow_schedules_async()

    async def _runs(workflow_id: str) -> list[dict[str, Any]]:
        resp = await tenant_client.get(f"{API}/runs", params={"workflow_id": workflow_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items: list[dict[str, Any]] = body["items"] if isinstance(body, dict) else body
        return [r for r in items if str(r.get("workflow_id")) == workflow_id]

    fired = await _runs(wid)
    assert len(fired) == 1, fired
    detail = (await tenant_client.get(f"{API}/runs/{fired[0]['run_id']}")).json()
    assert detail["trigger_type"] == "schedule"
    assert detail["inputs"] == {"team": "platform", "week": "W40"}

    # Required input with no value and no default: a visible FAILED run naming
    # the input, not a silently skipped occurrence (or a run with '' inputs).
    rejected = await _runs(req_id)
    assert len(rejected) == 1, rejected
    detail = (await tenant_client.get(f"{API}/runs/{rejected[0]['run_id']}")).json()
    assert detail["status"] == "failed"
    assert "facts" in (detail.get("error") or "")
