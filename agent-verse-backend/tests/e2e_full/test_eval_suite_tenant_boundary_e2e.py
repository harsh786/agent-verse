"""e2e_full: eval suites are per-tenant rows, and a run never blocks the request.

Regressions for the cross-tenant sweep findings on ``/intelligence/eval-suites``:
suites lived in one process-wide dict keyed by suite id, so any tenant could
list, extend, run and read the results of any other tenant's suite, and
``POST /run`` executed every golden goal inside the request (the sweep saw it
hang). Suites and runs now live in ``eval_suites`` / ``eval_suite_results``
under RLS; a run answers 202 and is finished by a background task.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _signup(app: Any, client: Any) -> AsyncClient:
    email = f"eval-boundary-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "Eval Boundary", "email": email})
    assert r.status_code == 201, r.text
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://e2e-full",
        headers={"X-API-Key": r.json()["api_key"]},
    )


@pytest.fixture
def _no_celery_worker(app: Any) -> Any:
    """This tier runs no Celery worker. Since MEM-53 a run is advanced on Celery
    whenever goals are queued to Celery, which here would sit unconsumed; with the
    goal queue off the same durable steps run in-process (the single-process
    executor), so the run really finishes."""
    gs = app.state.goal_service
    prev_queue = gs._task_queue
    gs._task_queue = None
    try:
        yield
    finally:
        gs._task_queue = prev_queue


async def test_eval_suites_are_isolated_and_runs_are_async(
    app: Any, client: Any, _no_celery_worker: None
) -> None:
    owner = await _signup(app, client)
    other = await _signup(app, client)
    try:
        created = await owner.post(
            "/intelligence/eval-suites",
            json={"suite_id": "shared-name", "name": "Regression", "description": "nightly"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["description"] == "nightly"
        # Ids are per tenant: the other tenant can use the same id without
        # colliding with (or learning about) the owner's suite.
        mine = await other.post("/intelligence/eval-suites", json={"suite_id": "shared-name"})
        assert mine.status_code == 201, mine.text
        dup = await owner.post("/intelligence/eval-suites", json={"suite_id": "shared-name"})
        assert dup.status_code == 409

        add = await owner.post(
            "/intelligence/eval-suites/shared-name/tasks",
            json={
                "goal": "Summarise the onboarding policy",
                "expected_output_contains": ["onboarding"],
                "tags": ["smoke"],
            },
        )
        assert add.status_code == 201, add.text

        # The other tenant sees only its own (empty) suite of that name.
        theirs = (await other.get("/intelligence/eval-suites/shared-name")).json()
        assert theirs["task_count"] == 0 and theirs["tasks"] == []
        listed = (await other.get("/intelligence/eval-suites")).json()
        assert [s["suite_id"] for s in listed] == ["shared-name"]
        assert listed[0]["task_count"] == 0

        # Run: answered immediately, finished in the background, durable.
        t0 = time.monotonic()
        started = await owner.post("/intelligence/eval-suites/shared-name/run")
        assert started.status_code == 202, started.text
        assert time.monotonic() - t0 < 10, "run must not execute inside the request"
        run_id = started.json()["run_id"]
        assert started.json()["total"] == 1
        assert started.json()["executor"] == "in_process"

        deadline = time.monotonic() + 120
        runs: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            resp = await owner.get("/intelligence/eval-suites/shared-name/results")
            if resp.status_code == 429:
                # The plan rate limit is per tenant (not per path): poll politely.
                await asyncio.sleep(float(resp.headers.get("Retry-After", "5")))
                continue
            runs = resp.json()
            if runs and runs[0]["status"] != "running":
                break
            await asyncio.sleep(3)
        assert runs and runs[0]["run_id"] == run_id
        assert runs[0]["status"] in ("completed", "failed"), runs[0]
        assert runs[0]["passed"] + runs[0]["failed"] == 1 or runs[0]["status"] == "failed"

        # The other tenant's same-named suite has no runs, and deleting its own
        # suite leaves the owner's untouched.
        assert (await other.get("/intelligence/eval-suites/shared-name/results")).json() == []
        assert (await other.delete("/intelligence/eval-suites/shared-name")).status_code == 204
        still = await owner.get("/intelligence/eval-suites/shared-name")
        assert still.status_code == 200 and still.json()["task_count"] == 1
        # A suite the caller does not have: 404 everywhere, never a write.
        for method, path, body in [
            ("GET", "/intelligence/eval-suites/shared-name", None),
            ("POST", "/intelligence/eval-suites/shared-name/tasks", {"goal": "inject", "expected_tools": ["x"]}),
            ("POST", "/intelligence/eval-suites/shared-name/run", None),
            ("GET", "/intelligence/eval-suites/shared-name/results", None),
        ]:
            r = await other.request(method, path, json=body)
            assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text}"
        assert (await owner.get("/intelligence/eval-suites/shared-name")).json()["task_count"] == 1
    finally:
        await owner.aclose()
        await other.aclose()
