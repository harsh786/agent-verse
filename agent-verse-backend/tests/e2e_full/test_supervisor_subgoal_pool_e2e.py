"""e2e_full (CORE-09): a worker-run supervisor goal completes while the main goal
pool has a single slot, because its sub-goals run on the dedicated sub-goal pool.

The supervisor parent holds its Celery slot while it waits for its sub-goals.
Sub-goals used to go to the parent's own ``goals.{plan}`` queue, so with one slot
(the parent's) they were never picked up: the parent starved its children until
its per-subtask timeout. They now go to ``goals.subgoals.{plan}``, which only the
sub-goal pool consumes.

Two REAL ``celery worker`` processes run against the booted app's Postgres and
Redis: the main pool (``goals.{plan}``, ``--concurrency=1``) and the sub-goal
pool (``goals.subgoals.{plan}``). Both run from a temp dir (no developer .env)
with a deterministic scripted provider, so the supervisor really decomposes the
goal into two sub-goals without calling any LLM.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MAIN_QUEUES = "goals.free,goals.starter,goals.professional,goals.enterprise"
_SUBGOAL_QUEUES = (
    "goals.subgoals.free,goals.subgoals.starter,goals.subgoals.professional,"
    "goals.subgoals.enterprise"
)
_TERMINAL = {"complete", "failed", "cancelled"}

# Celery app module the workers load: the real app plus a deterministic provider.
# It is not a FakeProvider instance (the worker discards those in favour of its
# own canned three-reply fake, which cannot answer the decomposition prompt).
_BOOT_MODULE = textwrap.dedent(
    '''
    from app.scaling.celery_app import celery_app  # noqa: F401
    import app.providers.registry as _registry
    from app.providers.base import CompletionResponse
    from app.providers.fake import FakeProvider

    _DECOMPOSITION = (
        '{"sub_tasks": [{"goal": "List three facts about the ocean"},'
        ' {"goal": "List three facts about the desert"}]}'
    )


    class ScriptedProvider:
        def __init__(self):
            self._inner = FakeProvider(responses=[
                '{"steps": ["Execute the goal autonomously"]}',
                "Goal executed via Celery worker",
                '{"success": true, "reason": "Completed by worker"}',
            ])

        async def complete(self, request):
            text = " ".join(str(m.content) for m in request.messages)
            if "goal decomposer" in text:
                content = _DECOMPOSITION
            elif "Synthesize a coherent" in text:
                content = "Synthesized answer from both sub-goals."
            else:
                return await self._inner.complete(request)
            return CompletionResponse(
                content=content, model=request.model, input_tokens=10, output_tokens=8
            )

        def __getattr__(self, name):
            return getattr(self._inner, name)


    _registry.resolve_provider = lambda *args, **kwargs: ScriptedProvider()
    '''
)


def _start_worker(workdir: Path, name: str, queues: str, concurrency: int) -> dict[str, Any]:
    log_path = workdir / f"{name}.log"
    env = dict(os.environ)
    # Run from the temp dir (no .env there): from the backend root the worker would
    # load the developer's .env and call real LLM providers.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(workdir), str(_BACKEND_ROOT), env.get("PYTHONPATH", "")]
    )
    assert env.get("DATABASE_URL") and env.get("REDIS_URL"), "app fixture must export DSNs"
    env["ENVIRONMENT"] = "development"  # FakeProvider is refused in production
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "e2e_supervisor_boot:celery_app", "worker",
            "-Q", queues, "--loglevel=info", f"--concurrency={concurrency}",
            "-n", f"{name}@%h",
        ],
        cwd=str(workdir),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"proc": proc, "log_path": log_path, "log_file": log_file}


def _wait_ready(worker: dict[str, Any]) -> None:
    proc, log_path = worker["proc"], worker["log_path"]
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"worker exited: {log_path.read_text()[-3000:]}")
        with contextlib.suppress(OSError):
            if "ready." in log_path.read_text():
                return
        time.sleep(0.5)
    raise RuntimeError(f"worker did not become ready: {log_path.read_text()[-3000:]}")


def _stop_worker(worker: dict[str, Any]) -> None:
    proc = worker["proc"]
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    worker["log_file"].close()


@pytest.fixture(scope="module")
def worker_pools(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    workdir = tmp_path_factory.mktemp("supervisorpools")
    (workdir / "e2e_supervisor_boot.py").write_text(_BOOT_MODULE)
    main = _start_worker(workdir, "mainpool", _MAIN_QUEUES, concurrency=1)
    sub = _start_worker(workdir, "subgoalpool", _SUBGOAL_QUEUES, concurrency=1)
    try:
        _wait_ready(main)
        _wait_ready(sub)
        yield {"main": main, "sub": sub}
    finally:
        _stop_worker(main)
        _stop_worker(sub)


def _goal_ids_run(log_text: str) -> set[str]:
    return set(re.findall(r"Running goal ([0-9a-fA-F-]{8,}) for tenant", log_text))


async def test_supervisor_goal_completes_with_a_single_main_pool_slot(
    app: Any, tenant_client: Any, worker_pools: dict[str, Any]
) -> None:
    assert app.state.goal_service._task_queue is not None, "goals must go through Celery"

    created = await tenant_client.post(
        "/agents", json={"name": "coordinator", "enable_supervisor": True}
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    resp = await tenant_client.post(
        "/goals", json={"goal": "Compare the ocean and the desert", "agent_id": agent_id}
    )
    assert resp.status_code == 202, resp.text
    parent_id = resp.json()["goal_id"]

    deadline = asyncio.get_event_loop().time() + 240
    goal: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{parent_id}")
        if got.status_code == 200:
            goal = got.json()
            if goal.get("status") in _TERMINAL:
                break
        await asyncio.sleep(1.0)

    main_log = worker_pools["main"]["log_path"].read_text()
    sub_log = worker_pools["sub"]["log_path"].read_text()
    logs = f"--- main pool ---\n{main_log[-4000:]}\n--- sub-goal pool ---\n{sub_log[-4000:]}"
    assert goal.get("status") == "complete", f"{goal!r}\n{logs}"

    # The parent ran on the (single-slot) main pool; both sub-goals ran on the
    # dedicated pool, never on the main one.
    main_ran, sub_ran = _goal_ids_run(main_log), _goal_ids_run(sub_log)
    assert main_ran == {parent_id}, logs
    assert parent_id not in sub_ran and len(sub_ran) == 2, logs

    # The supervisor really fanned out, and every sub-goal succeeded.
    me = (await tenant_client.get("/tenants/me")).json()
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id=str(me["tenant_id"]), plan=PlanTier.FREE, api_key_id="")
    for sub_id in sub_ran:
        sub_goal = await app.state.goal_service.get_goal(goal_id=sub_id, tenant_ctx=ctx)
        assert str(sub_goal.get("status")) == "complete", sub_goal
    events = await app.state.goal_service.get_events(goal_id=parent_id, tenant_ctx=ctx)
    done = [e for e in events if e.get("type") == "supervisor_complete"]
    assert done, [e.get("type") for e in events]
    assert done[-1].get("success") is True and done[-1].get("completed_tasks") == 2, done


async def _child_goal_ids(parent_id: str) -> list[str]:
    import asyncpg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT id FROM goals WHERE parent_goal_id = $1", parent_id)
    finally:
        await conn.close()
    return [str(r["id"]) for r in rows]


async def test_supervisor_mode_submission_enqueues_a_parent_goal(
    app: Any, tenant_client: Any, worker_pools: dict[str, Any]
) -> None:
    """CORE-07: POST /goals workflow_mode=supervisor returns a real parent goal id
    at once (no in-request fan-out); the parent runs on a worker and its sub-goals
    are linked to it (goals.parent_goal_id) and run on the sub-goal pool."""
    started = time.monotonic()
    resp = await tenant_client.post(
        "/goals",
        json={"goal": "Contrast the ocean with the desert", "workflow_mode": "supervisor"},
    )
    assert resp.status_code == 202, resp.text
    assert time.monotonic() - started < 10, "the request must not wait for the sub-goals"
    parent_id = resp.json()["goal_id"]
    assert parent_id and resp.json()["mode"] == "supervisor"

    deadline = asyncio.get_event_loop().time() + 240
    goal: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await tenant_client.get(f"/goals/{parent_id}")
        if got.status_code == 200:
            goal = got.json()
            if goal.get("status") in _TERMINAL:
                break
        await asyncio.sleep(1.0)
    main_log = worker_pools["main"]["log_path"].read_text()
    sub_log = worker_pools["sub"]["log_path"].read_text()
    logs = f"--- main pool ---\n{main_log[-4000:]}\n--- sub-goal pool ---\n{sub_log[-4000:]}"
    assert goal.get("status") == "complete", f"{goal!r}\n{logs}"

    children = await _child_goal_ids(parent_id)
    assert len(children) == 2, (children, logs)
    assert parent_id in _goal_ids_run(main_log)
    for child in children:
        assert child in _goal_ids_run(sub_log) and child not in _goal_ids_run(main_log), logs
