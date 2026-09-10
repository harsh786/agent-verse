"""e2e_full (gap #2): CROSS-PROCESS workflow HITL — pause in a real Celery
worker, approve over the API, resume to terminal in a worker.

``test_workflow_hitl_e2e`` proves the approval-gated mechanism *in-process*
(``runner._celery`` nulled) because, as its docstring records, cross-process HITL
was impossible: the ``HITLWorkflowGateway`` kept approvals in an in-memory
per-process dict and the LangGraph checkpointer was per-process, so a run
suspended in a worker created its approval where the API could not see it, and an
API decision updated a checkpoint the worker never read.

This test proves the gap is closed. The run executes in a genuine, out-of-process
``celery ... worker`` subprocess (same pattern as ``test_workflow_run_e2e``):

* ``POST /workflows/{id}/trigger`` (``dry_run=false``) dispatches to the worker,
  which suspends at the approval gate and creates the pending approval in the
  durable, RLS-scoped ``workflow_approvals`` Postgres store (migration 0119).
* The API's ``GET /approvals`` **sees that worker-created pending approval** —
  cross-process visibility, worker → API.
* ``POST /approvals/{id}/decide`` records the decision in Postgres and dispatches
  a resume task; a worker reconstructs the run from the persisted record + the
  decision (``WorkflowRunner.execute_resume_fresh`` — no shared checkpointer, no
  RediSearch) and drives it to terminal.
* ``GET /runs/{id}`` reaches ``complete`` — resumed in a worker, not in the test
  process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_WORKER_QUEUES = (
    "workflows.free,workflows.starter,workflows.professional,"
    "workflows.enterprise,workflows.maintenance"
)


@pytest.fixture(scope="module")
def celery_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    """Launch a real out-of-process Celery worker for the workflow queues.

    Binds the worker to the SAME Postgres + Redis the booted app uses (the
    session ``app`` fixture exported ``DATABASE_URL`` / ``REDIS_URL`` into
    ``os.environ``), and forwards that environment to the subprocess.
    """
    log_path = tmp_path_factory.mktemp("xhitlworker") / "worker.log"
    env = dict(os.environ)
    assert env.get("DATABASE_URL"), "DATABASE_URL not set by the app fixture"
    assert env.get("REDIS_URL"), "REDIS_URL not set by the app fixture"

    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.scaling.celery_app",
        "worker",
        "-Q",
        _WORKER_QUEUES,
        "--loglevel=info",
        "--concurrency=1",
        "-n",
        "xhitle2e@%h",
    ]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        cwd=str(_BACKEND_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                text = log_path.read_text()
            except OSError:
                text = ""
            if "ready." in text:
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            tail = ""
            with contextlib.suppress(OSError):
                tail = log_path.read_text()[-3000:]
            raise RuntimeError(
                f"celery worker did not become ready in time (rc={proc.poll()}).\n"
                f"--- worker log tail ---\n{tail}"
            )
        yield {"proc": proc, "log_path": log_path}
    finally:
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        log_file.close()


async def _create_hitl_workflow(tenant_client: Any) -> str:
    body = {
        "name": f"xhitl-{uuid.uuid4().hex[:8]}",
        "description": "cross-process approval-gated workflow",
        "definition": {
            "name": "Cross-proc HITL WF",
            # A single approval gate assigned to the default API caller
            # ("anonymous") so the API's assignee-filtered GET /approvals surfaces
            # the pending approval the *worker* creates.
            "steps": [
                {
                    "id": "gate",
                    "type": "hitl",
                    "assignee": {"strategy": "specific", "specific_user": "anonymous"},
                    "actions": [{"id": "approve"}, {"id": "reject"}],
                }
            ],
        },
    }
    resp = await tenant_client.post(f"{_API}/workflows", json=body)
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _poll_run_status(
    tenant_client: Any, run_id: str, wanted: set[str], *, timeout: float = 60.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"{_API}/runs/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in wanted:
                return last
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"run {run_id} did not reach {wanted} within {timeout}s; last={last!r}"
    )


async def _find_pending_via_api(
    tenant_client: Any, run_id: str, *, timeout: float = 30.0
) -> str:
    """Return the pending approval id the WORKER created, seen over the API.

    This is the cross-process visibility proof: the approval is read from the
    durable Postgres store the worker wrote to, via the API's /approvals inbox.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    last: Any = None
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"{_API}/approvals")
        if resp.status_code == 200:
            last = resp.json()
            for item in last.get("items", []):
                if item.get("run_id") == run_id and item.get("status") == "pending":
                    return str(item["request_id"])
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"no pending approval for run {run_id} surfaced by GET /approvals "
        f"within {timeout}s; last={last!r}"
    )


async def test_crossproc_hitl_pause_approve_resume(
    tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    workflow_id = await _create_hitl_workflow(tenant_client)

    # Trigger a REAL run (dry_run=false): dispatched to the out-of-process worker.
    trig = await tenant_client.post(
        f"{_API}/workflows/{workflow_id}/trigger",
        json={"inputs": {}, "dry_run": False},
    )
    assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
    run_id = trig.json()["run_id"]
    assert run_id

    # ── PAUSED in the worker: the run is waiting on a human ────────────────────
    paused = await _poll_run_status(tenant_client, run_id, {"waiting_hitl"})
    assert paused["status"] == "waiting_hitl", f"expected pause at gate, got {paused!r}"

    # ── Cross-process visibility: the API sees the WORKER-created approval ─────
    request_id = await _find_pending_via_api(tenant_client, run_id)

    # GET /approvals/{id} detail also resolves it (from Postgres, cross-process).
    detail = await tenant_client.get(f"{_API}/approvals/{request_id}")
    assert detail.status_code == 200, f"{detail.status_code} {detail.text}"
    assert detail.json()["run_id"] == run_id

    # ── APPROVE via the HTTP API ───────────────────────────────────────────────
    decide = await tenant_client.post(
        f"{_API}/approvals/{request_id}/decide",
        json={"action": "approve", "note": "ship it"},
    )
    assert decide.status_code == 200, f"decide failed: {decide.status_code} {decide.text}"
    assert decide.json().get("action_taken") == "approve"

    # ── RESUMED to terminal in a worker (not this process) ─────────────────────
    resumed = await _poll_run_status(tenant_client, run_id, {"complete"})
    assert resumed["status"] == "complete", (
        f"approved run should resume to complete in a worker, got {resumed!r}"
    )

    # The approval is resolved and durably reflects the decision.
    got = await tenant_client.get(f"{_API}/approvals/{request_id}")
    assert got.status_code == 200
    assert got.json()["status"] != "pending"
    assert got.json()["action_taken"] == "approve"

    # Proof the work really happened in the worker subprocess: the task ran and
    # succeeded there (both the fresh run and the resume are the same task name).
    log_text = celery_worker["log_path"].read_text()
    assert "workflow.execute_workflow_run" in log_text, "worker never received the task"
    assert "succeeded" in log_text, "worker task did not report success"
