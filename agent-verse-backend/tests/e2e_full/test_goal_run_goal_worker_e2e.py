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
import os
import sys
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
    from tests._worker_procs import node_name, worker_process

    workdir = tmp_path_factory.mktemp("goalworker")
    env = dict(os.environ)
    # Run from the temp dir (no .env there): from the backend root the worker would
    # load the developer's .env and call real LLM providers.
    env["PYTHONPATH"] = str(_BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    assert env.get("DATABASE_URL") and env.get("REDIS_URL"), "app fixture must export DSNs"
    env["ENVIRONMENT"] = "development"  # FakeProvider is refused in production
    # USR-7: the whole process group is stopped on any exit (even after the
    # worker's main process died), and a failed start stops what it started.
    with worker_process(
        [
            sys.executable, "-m", "celery", "-A", "app.scaling.celery_app", "worker",
            "-Q", _GOAL_QUEUES, "--loglevel=info", "--concurrency=1",
            "-n", node_name("goale2e"),
        ],
        cwd=workdir,
        env=env,
        log_path=workdir / "worker.log",
        ready_timeout=60.0,
        name="goale2e",
    ) as handle:
        yield handle.as_dict()


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
