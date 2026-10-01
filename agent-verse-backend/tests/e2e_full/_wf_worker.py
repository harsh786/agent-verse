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
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
    log_path = log_dir / "worker.log"
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
        f"{name}@%h",
    ]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        cwd=str(log_dir),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 300.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            with contextlib.suppress(OSError):
                if "ready." in log_path.read_text():
                    ready = True
                    break
            time.sleep(0.5)
        if not ready:
            tail = ""
            with contextlib.suppress(OSError):
                tail = log_path.read_text()[-3000:]
            raise RuntimeError(
                f"celery worker did not become ready (rc={proc.poll()}).\n{tail}"
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
