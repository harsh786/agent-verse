"""e2e_full (WS-4): a workflow run executes in a REAL, out-of-process Celery worker.

Unlike ``test_workflow_trigger_e2e`` — which papers over the missing harness
worker by nulling ``runner._celery`` and running the graph inline in the test
process — this test spins up a genuine ``celery ... worker`` **subprocess** bound
to the same Postgres + Redis the booted app uses, and triggers a real
(``dry_run=false``) run over the HTTP API. The run is dispatched onto the
``workflows.free`` queue and executed by that worker; the test only observes the
persisted result over HTTP.

What this proves end-to-end, against real infra:

* ``POST /workflows/{id}/trigger`` (``dry_run=false``) dispatches to Celery
  (``runner._celery`` is **left wired** — no inline shortcut).
* The out-of-process worker executes the run and persists **real** step results
  (2 rows for a 2-step workflow) — none of them the in-memory fallback's
  ``{"_mock": true}`` mock output.
* The run reaches a terminal (``complete``) status, queryable over HTTP.

Worker/checkpointer decision: the worker runs on its own per-process
``MemorySaver`` (LangGraph's async-capable in-memory checkpointer — the default
``_WORKER_CHECKPOINTER``), which needs no RediSearch. A fresh Celery-dispatched
run reconstructs its initial ``WorkflowState`` from the persisted run record via
``WorkflowRunner.execute_fresh`` (the dispatch branch of ``run()`` intentionally
does not seed the checkpointer, and the worker cannot read anything the API
process wrote), so the worker genuinely executes real steps.
"""

from __future__ import annotations

import contextlib
import json
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
# The worker must subscribe to every per-plan workflow queue the runner may
# dispatch onto (default tenant plan is "free" -> workflows.free).
_WORKER_QUEUES = (
    "workflows.free,workflows.starter,workflows.professional,"
    "workflows.enterprise,workflows.maintenance"
)


@pytest.fixture(scope="module")
def celery_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    """Launch a real out-of-process Celery worker for the workflow queues.

    Binds the worker to the SAME Postgres + Redis the booted app uses: the
    session ``app`` fixture has already exported ``DATABASE_URL`` / ``REDIS_URL``
    into ``os.environ`` (whether those came from ``E2E_*`` env or ephemeral
    testcontainers), and we forward that environment to the subprocess.
    """
    log_path = tmp_path_factory.mktemp("ws4worker") / "worker.log"
    env = dict(os.environ)
    # Belt-and-braces: these are set by the app fixture, assert they're present
    # so a mis-wired harness fails loudly instead of the worker silently using a
    # different backend than the app under test.
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
        "ws4e2e@%h",
    ]
    log_file = open(log_path, "w")  # noqa: SIM115 — closed in finally
    proc = subprocess.Popen(
        cmd,
        cwd=str(_BACKEND_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group so we can signal the whole tree
    )
    try:
        # Wait for the worker to report ready (celery prints "<node> ready.").
        deadline = time.monotonic() + 60.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # worker died on startup
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


async def _create_two_step_workflow(tenant_client: Any) -> str:
    body = {
        "name": f"ws4-run-{uuid.uuid4().hex[:8]}",
        "description": "WS-4 real-worker run",
        "definition": {
            "name": "WS-4 Run WF",
            # Two self-contained transform steps: no external tool, deterministic,
            # each persists a real step-result row when executed by the worker.
            "steps": [
                {"id": "s1", "type": "transform", "input": {"greeting": "hello"}},
                {
                    "id": "s2",
                    "type": "transform",
                    "input": {"greeting": "world"},
                    "depends_on": ["s1"],
                },
            ],
        },
    }
    resp = await tenant_client.post(f"{_API}/workflows", json=body)
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _poll_run_terminal(
    tenant_client: Any, run_id: str, *, timeout: float = 45.0
) -> dict[str, Any]:
    import asyncio

    terminal = {"complete", "failed", "cancelled", "timed_out"}
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"{_API}/runs/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in terminal:
                return last
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"run {run_id} did not reach a terminal status within {timeout}s; last={last!r}"
    )


async def test_workflow_run_executes_in_real_worker(
    tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    workflow_id = await _create_two_step_workflow(tenant_client)

    # Trigger a REAL run (dry_run=false). runner._celery stays wired, so this
    # dispatches onto the workflows.free queue for the out-of-process worker.
    trig = await tenant_client.post(
        f"{_API}/workflows/{workflow_id}/trigger",
        json={"inputs": {}, "dry_run": False},
    )
    assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
    run_id = trig.json()["run_id"]
    assert run_id

    # The worker drives the run to a terminal state, observable over HTTP.
    final = await _poll_run_terminal(tenant_client, run_id)
    assert final["status"] == "complete", f"expected complete, got {final!r}"

    # Two REAL step rows were persisted by the worker.
    steps_resp = await tenant_client.get(f"{_API}/runs/{run_id}/steps")
    assert steps_resp.status_code == 200, f"{steps_resp.status_code} {steps_resp.text}"
    steps = steps_resp.json()
    step_ids = {s["step_id"] for s in steps}
    assert step_ids == {"s1", "s2"}, f"expected 2 real step rows s1,s2; got {step_ids}"

    # None of the step outputs is the in-memory fallback's mock output. The
    # fallback runner (used when no DB run store is wired) emits {"_mock": true};
    # its absence proves the DB-backed worker path actually ran real steps.
    for s in steps:
        assert s["status"] == "complete", f"step {s['step_id']} not complete: {s!r}"
        blob = json.dumps(s.get("output") or {})
        assert '"_mock"' not in blob, f"step {s['step_id']} carries mock output: {blob}"

    # Marker proof that the run really executed in the worker subprocess.
    log_text = celery_worker["log_path"].read_text()
    assert "workflow.execute_workflow_run" in log_text, "worker never received the task"
    assert "succeeded" in log_text, "worker task did not report success"
