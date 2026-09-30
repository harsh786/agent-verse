"""e2e_full (WF-20): a submitted goal runs through the REAL Celery ``run_goal``
in an out-of-process worker and reaches a terminal status with its lifecycle
events persisted.

Other tests drive AgentGraph directly or mock the worker's claim, so the
production path — POST /goals -> CeleryGoalTaskQueue -> broker -> ``celery
worker`` -> run_goal (lock, atomic claim, status bridge, FakeProvider since no
LLM key is configured) -> goal row + events in Postgres — was unproven end to
end. The worker is bound to the same Postgres + Redis as the booted app.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_GOAL_QUEUES = "goals.free,goals.starter,goals.professional,goals.enterprise"
_TERMINAL = {"complete", "failed", "cancelled"}


@pytest.fixture(scope="module")
def goal_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    log_path = tmp_path_factory.mktemp("goalworker") / "worker.log"
    env = dict(os.environ)
    # Run from the temp dir (no .env there): from the backend root the worker would
    # load the developer's .env and call real LLM providers.
    env["PYTHONPATH"] = str(_BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    assert env.get("DATABASE_URL") and env.get("REDIS_URL"), "app fixture must export DSNs"
    env["ENVIRONMENT"] = "development"  # FakeProvider is refused in production
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "app.scaling.celery_app", "worker",
            "-Q", _GOAL_QUEUES, "--loglevel=info", "--concurrency=1", "-n", "goale2e@%h",
        ],
        cwd=str(log_path.parent),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            with contextlib.suppress(OSError):
                if "ready." in log_path.read_text():
                    break
            time.sleep(0.5)
        else:
            raise RuntimeError("goal worker did not become ready")
        if proc.poll() is not None:
            raise RuntimeError(f"goal worker exited: {log_path.read_text()[-3000:]}")
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


async def test_goal_runs_to_terminal_in_a_real_run_goal_worker(
    app: Any, tenant_client: Any, goal_worker: dict[str, Any]
) -> None:
    from app.tenancy.context import PlanTier, TenantContext

    assert app.state.goal_service._task_queue is not None, "goals must go through Celery"

    resp = await tenant_client.post("/goals", json={"goal": "Summarize the quarterly report"})
    assert resp.status_code == 202, resp.text
    goal_id = resp.json()["goal_id"]

    deadline = asyncio.get_event_loop().time() + 120
    goal: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            goal = got.json()
            if goal.get("status") in _TERMINAL:
                break
        await asyncio.sleep(1.0)
    log_text = goal_worker["log_path"].read_text()
    assert goal.get("status") in _TERMINAL, (
        f"goal never reached a terminal status: {goal!r}\n--- worker log ---\n{log_text[-4000:]}"
    )
    assert goal["status"] == "complete", f"{goal!r}\n--- worker log ---\n{log_text[-4000:]}"

    # The worker really ran it (not the API process).
    assert "app.scaling.tasks.run_goal" in log_text and "succeeded" in log_text

    # Lifecycle events the worker emitted are durably persisted.
    me = (await tenant_client.get("/tenants/me")).json()
    ctx = TenantContext(tenant_id=str(me["tenant_id"]), plan=PlanTier.FREE, api_key_id="")
    events = await app.state.goal_service.get_events(goal_id=goal_id, tenant_ctx=ctx)
    types = [e.get("type") for e in events]
    assert types, "no goal events persisted"
    assert any(t in ("goal_complete", "goal_completed", "complete") for t in types), types
