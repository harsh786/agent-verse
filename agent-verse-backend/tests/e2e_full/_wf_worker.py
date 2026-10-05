"""Shared helpers for e2e_full tests that need a REAL out-of-process workflow
Celery worker (same pattern as ``test_workflow_hitl_crossproc_e2e``).

The worker binds to the Postgres + Redis the booted ``app`` fixture exported
into ``os.environ`` and runs from a temp dir (no developer ``.env`` there, so it
can never reach a real LLM provider).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests._worker_procs import node_name, worker_process

API = "/api/v1"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKER_QUEUES = (
    "workflows.free,workflows.starter,workflows.professional,"
    "workflows.enterprise,workflows.maintenance"
)


@contextlib.contextmanager
def workflow_worker(
    log_dir: Path,
    *,
    name: str,
    extra_env: dict[str, str] | None = None,
    concurrency: int = 1,
) -> Iterator[dict[str, Any]]:
    """A real workflow worker, stopped (whole process group) on any exit — USR-7."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
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
        WORKER_QUEUES,
        "--loglevel=info",
        f"--concurrency={concurrency}",
        "-n",
        node_name(name),
    ]
    with worker_process(
        cmd, cwd=log_dir, env=env, log_path=log_dir / "worker.log", name=name
    ) as handle:
        yield handle.as_dict()


async def create_workflow(client: Any, definition: dict[str, Any], *, name: str) -> str:
    resp = await client.post(
        f"{API}/workflows", json={"name": name, "description": "", "definition": definition}
    )
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def poll_run(
    client: Any, run_id: str, wanted: set[str], *, timeout: float = 60.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"{API}/runs/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in wanted:
                return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"run {run_id} did not reach {wanted} in {timeout}s; last={last!r}")


async def pending_approval_for(
    client: Any, run_id: str, *, timeout: float = 30.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: Any = None
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"{API}/approvals")
        if resp.status_code == 200:
            last = resp.json()
            for item in last.get("items", []):
                if item.get("run_id") == run_id and item.get("status") == "pending":
                    return dict(item)
        await asyncio.sleep(0.5)
    raise AssertionError(f"no pending approval for run {run_id}; last={last!r}")
