"""e2e_full (GOAL-STALL / RW-21): a goal never stalls silently on a real worker.

Live evidence: goals sat ``executing`` for 7+ minutes with no events. The worker
running them died (SIGABRT, WorkerLostError); the redelivered task found the dead
runner's Redis lock and skipped; nothing noticed for the plan's 1 h goal timeout.

Real ``celery worker`` processes run against the booted app's Postgres + Redis
(from a temp dir, no developer .env) with a deterministic provider whose executor
call HANGS:

1. step deadline — the hung step emits ``step_heartbeat`` events, is cancelled at
   its deadline (``step_timeout`` / ``step_failed``) and the goal fails within
   seconds instead of hanging; the run kept ``goals.heartbeat_at`` fresh;
2. dead worker — the worker is SIGKILLed mid-step; its heartbeat goes stale; the
   beat reaper task releases the dead run's lock and requeues the goal (no tool
   had run), and a healthy worker completes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
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
_QUEUES = "goals.free,goals.starter,goals.professional,goals.enterprise"
_TERMINAL = {"complete", "failed", "cancelled"}

_BOOT_MODULE = textwrap.dedent(
    '''
    import asyncio
    import os

    from app.scaling.celery_app import celery_app  # noqa: F401
    import app.providers.registry as _registry
    from app.providers.base import CompletionResponse
    from app.providers.fake import FakeProvider

    _HANG = os.environ.get("E2E_PROVIDER_MODE") == "hang"


    def _reply(request):
        schema = getattr(request, "response_schema", None) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            return '{"steps": ["Summarize the quarterly weather report"]}'
        if "success" in props:
            return '{"success": true, "reason": "summary produced"}'
        return "The quarterly weather report was mild with two storms."


    class StallProvider:
        """Plans and verifies normally; the executor call hangs when _HANG.

        Not a FakeProvider instance: the worker discards those in favour of its
        own canned fake (see the sub-goal pool e2e)."""

        def __init__(self):
            self._inner = FakeProvider(responses=["unused"])

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def _is_execution(self, request):
            schema = getattr(request, "response_schema", None) or {}
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            return "steps" not in props and "success" not in props

        async def complete(self, request):
            if _HANG and self._is_execution(request):
                await asyncio.Event().wait()
            content = _reply(request)
            return CompletionResponse(content=content, model=request.model,
                                      input_tokens=5, output_tokens=5)

        async def stream_tokens(self, request, on_token):
            if _HANG:
                await asyncio.Event().wait()
            content = _reply(request)
            await on_token(content)
            return CompletionResponse(content=content, model=request.model,
                                      input_tokens=5, output_tokens=5)

        async def stream_complete(self, request):
            if _HANG:
                await asyncio.Event().wait()
            yield _reply(request)


    _registry.resolve_provider = lambda *args, **kwargs: StallProvider()
    '''
)


def _start_worker(workdir: Path, name: str, extra_env: dict[str, str]) -> dict[str, Any]:
    log_path = workdir / f"{name}.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(workdir), str(_BACKEND_ROOT), env.get("PYTHONPATH", "")]
    )
    assert env.get("DATABASE_URL") and env.get("REDIS_URL"), "app fixture must export DSNs"
    env["ENVIRONMENT"] = "development"
    env.update(extra_env)
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "e2e_stall_boot:celery_app", "worker",
            "-Q", _QUEUES, "--loglevel=info", "--concurrency=1", "-n", f"{name}@%h",
        ],
        cwd=str(workdir),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    worker = {"proc": proc, "log_path": log_path, "log_file": log_file}
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"worker exited: {log_path.read_text()[-3000:]}")
        with contextlib.suppress(OSError):
            if "ready." in log_path.read_text():
                return worker
        time.sleep(0.5)
    _stop_worker(worker, kill=True)
    raise RuntimeError(f"worker did not become ready: {log_path.read_text()[-3000:]}")


def _stop_worker(worker: dict[str, Any], *, kill: bool = False) -> None:
    proc = worker["proc"]
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL if kill else signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=10)
    with contextlib.suppress(Exception):
        worker["log_file"].close()


def _purge_goal_queues(redis_url: str) -> None:
    import redis

    client = redis.Redis.from_url(redis_url)
    try:
        for queue in _QUEUES.split(","):
            keys = [queue, *client.scan_iter(match=f"{queue}\x06\x16*")]
            client.delete(*keys)
    finally:
        client.close()


@pytest.fixture
def workdir(app: Any, tmp_path: Path) -> Iterator[Path]:
    _purge_goal_queues(os.environ["REDIS_URL"])
    (tmp_path / "e2e_stall_boot.py").write_text(_BOOT_MODULE)
    yield tmp_path


async def _agent(tenant_client: Any) -> str:
    resp = await tenant_client.post(
        "/agents", json={"name": "stall-e2e", "max_iterations": 2}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body.get("agent_id") or body.get("id"))


async def _goal_row(goal_id: str) -> dict[str, Any]:
    import asyncpg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, heartbeat_at, runner_token, error_message, "
            "now() - heartbeat_at AS age FROM goals WHERE id = $1",
            goal_id,
        )
        events = await conn.fetch(
            "SELECT event_type, payload::text AS payload FROM goal_events "
            "WHERE goal_id = $1 ORDER BY sequence",
            goal_id,
        )
    finally:
        await conn.close()
    return {**dict(row), "events": [(e["event_type"], e["payload"]) for e in events]}


async def _wait(goal_id: str, predicate: Any, timeout: float, logs: Any) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    row: dict[str, Any] = {}
    while time.monotonic() < deadline:
        row = await _goal_row(goal_id)
        if predicate(row):
            return row
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"condition not reached: status={row.get('status')} "
        f"events={[e for e, _ in row.get('events', [])]}\n{logs()}"
    )


async def test_hung_provider_step_hits_its_deadline_and_the_goal_ends(
    tenant_client: Any, workdir: Path
) -> None:
    worker = _start_worker(
        workdir,
        "hungpool",
        {
            "E2E_PROVIDER_MODE": "hang",
            "AGENT_STEP_TIMEOUT_SECONDS": "3",
            "AGENT_STEP_HEARTBEAT_SECONDS": "0.5",
            "GOAL_HEARTBEAT_INTERVAL_SECONDS": "0.5",
        },
    )
    try:
        agent_id = await _agent(tenant_client)
        resp = await tenant_client.post(
            "/goals", json={"goal": "Summarize the quarterly weather report",
                            "agent_id": agent_id}
        )
        assert resp.status_code == 202, resp.text
        goal_id = resp.json()["goal_id"]

        def logs() -> str:
            return worker["log_path"].read_text()[-4000:]

        # While the step hangs the run beats and the step heartbeats.
        live = await _wait(
            goal_id,
            lambda r: r["heartbeat_at"] is not None
            and any(e == "step_heartbeat" for e, _ in r["events"]),
            60,
            logs,
        )
        assert live["runner_token"], live
        assert live["age"].total_seconds() < 10

        started = time.monotonic()
        final = await _wait(goal_id, lambda r: r["status"] in _TERMINAL, 90, logs)
        assert final["status"] == "failed", final
        types = [e for e, _ in final["events"]]
        assert "step_timeout" in types, types
        failed = [p for e, p in final["events"] if e == "step_failed"]
        assert failed and "deadline" in failed[0], failed
        assert time.monotonic() - started < 80
    finally:
        _stop_worker(worker)


async def test_killed_worker_goal_is_reaped_and_completed_by_a_healthy_worker(
    tenant_client: Any, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hung = _start_worker(
        workdir,
        "dyingpool",
        {
            "E2E_PROVIDER_MODE": "hang",
            "AGENT_STEP_TIMEOUT_SECONDS": "600",
            "GOAL_HEARTBEAT_INTERVAL_SECONDS": "0.5",
        },
    )
    healthy: dict[str, Any] | None = None
    try:
        agent_id = await _agent(tenant_client)
        resp = await tenant_client.post(
            "/goals", json={"goal": "Summarize the quarterly weather report",
                            "agent_id": agent_id}
        )
        assert resp.status_code == 202, resp.text
        goal_id = resp.json()["goal_id"]

        def logs() -> str:
            text = hung["log_path"].read_text()[-3000:]
            if healthy is not None:
                text += "\n--- healthy ---\n" + healthy["log_path"].read_text()[-3000:]
            return text

        running = await _wait(
            goal_id,
            lambda r: r["status"] == "executing"
            and r["heartbeat_at"] is not None
            and any(e == "step_started" for e, _ in r["events"]),
            60,
            logs,
        )
        dead_token = running["runner_token"]
        import redis

        r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        assert r.get(f"goal_lock:{goal_id}") == dead_token

        # The worker dies mid-step (as the SIGABRT'd prefork child did).
        _stop_worker(hung, kill=True)
        await asyncio.sleep(3.0)
        stale = await _goal_row(goal_id)
        assert stale["status"] == "executing" and stale["age"].total_seconds() >= 2.5

        healthy = _start_worker(workdir, "healthypool", {"E2E_PROVIDER_MODE": "ok"})

        # The beat reaper (the real Celery task, run in-process) takes it over.
        monkeypatch.setenv("GOAL_HEARTBEAT_STALE_SECONDS", "2")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            from app.scaling import tasks

            # A sync Celery task drives its own event loop: run it off this one.
            result = await asyncio.to_thread(
                lambda: tasks.reap_stale_goal_runners.apply().get(timeout=60)
            )
        finally:
            get_settings.cache_clear()
        assert goal_id in result["requeued"], result
        assert r.get(f"goal_lock:{goal_id}") is None  # the dead run's lock is released

        final = await _wait(goal_id, lambda row: row["status"] in _TERMINAL, 120, logs)
        assert final["status"] == "complete", final
        types = [e for e, _ in final["events"]]
        assert "goal_runner_lost" in types, types
        r.close()
    finally:
        _stop_worker(hung, kill=True)
        if healthy is not None:
            _stop_worker(healthy)
