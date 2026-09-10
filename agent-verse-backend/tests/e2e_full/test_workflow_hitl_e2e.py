"""e2e_full (WS-4): workflow HITL pause -> approve (HTTP) -> resume to terminal.

Real-infra e2e of the approval-gated workflow mechanism that
``tests/workflow/test_hitl_runner_integration`` covers with in-memory doubles:
here the run is persisted in live Postgres, the signup / auth / dedup state lives
in live Redis, and the decision is submitted over the real workflow-HITL HTTP
API (``POST /approvals/{id}/decide``).

Why this run executes IN-PROCESS (and not on the out-of-process worker that
``test_workflow_run_e2e`` uses): the workflow-HITL mechanism is process-local by
construction —

* ``HITLWorkflowGateway`` stores pending approvals in an in-memory ``_store``
  dict (per process), and
* the LangGraph checkpointer holding the suspended run is per-process, while
* ``WorkflowRunner.resume_from_hitl`` applies the reviewer's decision
  (``aupdate_state``) in the process that handles the approval.

So a run suspended in a separate Celery worker would create its approval in the
*worker's* gateway (invisible to the API's ``/approvals``), and an API-side
approval would update a checkpoint the worker never sees. Cross-process HITL
would need a shared, DB-backed approval store + a shared async checkpointer —
neither exists today. This test therefore drives the genuine mechanism in the
API process (``runner._celery`` nulled for the duration, ``MemorySaver`` swapped
in because the harness Redis has no RediSearch so the app falls back to the
sync ``RedisSaver`` whose async API is ``NotImplementedError``) against real
Postgres + Redis. ``test_workflow_run_e2e`` is the out-of-process-worker proof.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"


@pytest.fixture
def _inprocess_hitl(app: Any) -> Iterator[None]:
    """Run this test's HITL workflow in-process (see module docstring).

    Nulls ``runner._celery`` so ``run()``/``resume_from_hitl`` execute inline in
    the API process — where the HITL gateway and checkpointer live — and swaps an
    async-capable ``MemorySaver`` into the compiler (the harness Redis lacks
    RediSearch, so the app's checkpointer is the sync ``RedisSaver`` whose
    ``aget_tuple`` raises ``NotImplementedError``). All state restored afterwards.
    """
    from langgraph.checkpoint.memory import MemorySaver

    runner = app.state.workflow_runner
    compiler = runner._compiler
    prev_celery = runner._celery
    prev_ckpt = compiler._checkpointer
    prev_cache = dict(compiler._cache)

    runner._celery = None
    compiler._checkpointer = MemorySaver()
    compiler._cache.clear()
    try:
        yield
    finally:
        runner._celery = prev_celery
        compiler._checkpointer = prev_ckpt
        compiler._cache.clear()
        compiler._cache.update(prev_cache)


async def _create_hitl_workflow(tenant_client: Any) -> str:
    body = {
        "name": f"ws4-hitl-{uuid.uuid4().hex[:8]}",
        "description": "WS-4 approval-gated workflow",
        "definition": {
            "name": "WS-4 HITL WF",
            # A single approval gate: the run suspends here until a reviewer
            # decides, then routes to END (mirrors the integration test's shape).
            "steps": [
                {
                    "id": "gate",
                    "type": "hitl",
                    "actions": [{"id": "approve"}, {"id": "reject"}],
                }
            ],
        },
    }
    resp = await tenant_client.post(f"{_API}/workflows", json=body)
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


def _pending_request_id(app: Any, run_id: str) -> str:
    """Find the pending approval the suspended run created (in-process gateway).

    The decision itself is submitted over the HTTP API; this only recovers the
    request id, which the assignee-filtered ``GET /approvals`` listing may not
    surface for the default 'anonymous' caller.
    """
    gateway = app.state.hitl_workflow_gateway
    for req in gateway._store.values():
        if req.run_id == run_id and req.status == "pending":
            return str(req.request_id)
    raise AssertionError(f"no pending approval found for run {run_id}")


async def test_hitl_workflow_pauses_then_resumes_on_approval(
    app: Any, tenant_client: Any, _inprocess_hitl: None
) -> None:
    workflow_id = await _create_hitl_workflow(tenant_client)

    # Trigger a real (non-dry) run. Inline execution suspends synchronously at
    # the gate, so by the time the request returns the run is paused.
    trig = await tenant_client.post(
        f"{_API}/workflows/{workflow_id}/trigger",
        json={"inputs": {}, "dry_run": False},
    )
    assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
    run_id = trig.json()["run_id"]

    # ── PAUSED: the run is waiting on a human, and a pending approval exists ───
    paused = await tenant_client.get(f"{_API}/runs/{run_id}")
    assert paused.status_code == 200, f"{paused.status_code} {paused.text}"
    assert paused.json()["status"] == "waiting_hitl", (
        f"expected the run to pause at the gate, got {paused.json()!r}"
    )
    request_id = _pending_request_id(app, run_id)

    # ── APPROVE via the workflow-HITL HTTP API ────────────────────────────────
    decide = await tenant_client.post(
        f"{_API}/approvals/{request_id}/decide",
        json={"action": "approve", "note": "ship it"},
    )
    assert decide.status_code == 200, f"decide failed: {decide.status_code} {decide.text}"
    assert decide.json().get("action_taken") == "approve"

    # ── RESUMED to terminal: the run is complete and no approval is pending ────
    resumed = await tenant_client.get(f"{_API}/runs/{run_id}")
    assert resumed.status_code == 200, f"{resumed.status_code} {resumed.text}"
    assert resumed.json()["status"] == "complete", (
        f"approved run should resume to complete, got {resumed.json()!r}"
    )

    # The approval is resolved — the gate step recorded the reviewer's action.
    gateway = app.state.hitl_workflow_gateway
    decided = await gateway.get_request(request_id)
    assert decided is not None and decided.status != "pending"
    assert decided.action_taken == "approve"
