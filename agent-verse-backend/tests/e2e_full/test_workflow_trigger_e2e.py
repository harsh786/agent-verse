"""e2e_full: workflow create → trigger → persisted run → queryable.

Proves the reconciliation of the two workflow persistence systems:

* ``POST /api/v1/workflows`` (WorkflowService → ``_WorkflowStore`` → the ``Workflow``
  ORM) persists the DSL in the **legacy ``workflows`` table** (Text id, JSONB
  ``definition``).
* The run engine (``WorkflowRunner`` + ``PostgresWorkflowRunStore``, migration
  0108) is built around the **``workflow_definitions`` table** (uuid id):
  ``workflow_runs.workflow_id`` has a FK to ``workflow_definitions(id)``.
* The create/update/delete path now **mirrors each workflow into
  ``workflow_definitions``** (same uuid id) via ``_WorkflowStore``'s bridge, and
  migration 0115 backfills pre-existing rows. So triggering an API-created
  workflow resolves the definition and creates a run without an FK violation.

The e2e harness has no Celery worker, so the ``_inline_runner`` fixture nulls
``workflow_runner._celery`` (mirroring how ``test_goal_hitl_lifecycle_e2e``
nulls ``goal_service._task_queue``); ``WorkflowRunner.run`` then executes the
run **inline** in-process. A real (non-``dry_run``) run is used deliberately —
the compiler skips step-result persistence for test runs, so proving
``/runs/{id}/steps`` requires a genuine inline execution.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"


@pytest.fixture
def _inline_runner(app: Any) -> Iterator[None]:
    """Run workflow triggers inline for one test (no Celery worker in the harness).

    Two harness realities are papered over here, both restored afterwards:

    * ``WorkflowRunner.run`` dispatches to Celery unless ``self._celery is None``;
      nulling it forces the inline path so the run executes in-process (mirrors
      ``test_goal_hitl_lifecycle_e2e``'s ``_task_queue`` nulling).
    * Inline execution drives the LangGraph checkpointer's *async* API. The e2e
      Redis (``redis:7-alpine``) has no RediSearch module, so the app falls back
      to a sync ``RedisSaver`` whose ``aget_tuple`` is ``NotImplementedError``.
      Production uses a RediSearch-enabled Redis + ``AsyncRedisSaver``; the
      harness swaps in an async-capable ``MemorySaver`` (cache cleared so the
      compiler recompiles against it).
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


async def _create_workflow(tenant_client: Any, *, step_id: str = "s1") -> str:
    body = {
        "name": f"e2e-wf-{uuid.uuid4().hex[:8]}",
        "description": "workflow trigger e2e",
        # A single self-contained transform step: succeeds inline with no external
        # tool, so the run reaches a terminal state and persists a step result.
        "definition": {
            "name": "E2E Trigger WF",
            "steps": [{"id": step_id, "type": "transform", "input": {"greeting": "hello"}}],
        },
    }
    resp = await tenant_client.post(f"{_API}/workflows", json=body)
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _trigger_and_step_ids(tenant_client: Any, workflow_id: str) -> set[str]:
    trig = await tenant_client.post(
        f"{_API}/workflows/{workflow_id}/trigger",
        json={"inputs": {}, "dry_run": False},
    )
    assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
    run_id = trig.json()["run_id"]
    steps = await tenant_client.get(f"{_API}/runs/{run_id}/steps")
    assert steps.status_code == 200, f"{steps.status_code} {steps.text}"
    return {s["step_id"] for s in steps.json()}


async def test_workflow_create_and_get_is_wired(tenant_client: Any) -> None:
    """The create/read path is genuinely wired (DB-backed, RLS-scoped)."""
    workflow_id = await _create_workflow(tenant_client)
    got = await tenant_client.get(f"{_API}/workflows/{workflow_id}")
    assert got.status_code == 200, f"{got.status_code} {got.text}"
    assert got.json()["id"] == workflow_id


async def test_trigger_creates_persisted_run(
    tenant_client: Any, _inline_runner: None
) -> None:
    workflow_id = await _create_workflow(tenant_client)

    # Trigger resolves the bridged definition and creates a run (no FK violation).
    trig = await tenant_client.post(
        f"{_API}/workflows/{workflow_id}/trigger",
        json={"inputs": {}, "dry_run": False},
    )
    assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
    run_id = trig.json()["run_id"]
    assert run_id

    # The run is persisted and queryable, scoped to the caller's tenant.
    got = await tenant_client.get(f"{_API}/runs/{run_id}")
    assert got.status_code == 200, f"{got.status_code} {got.text}"
    body = got.json()
    assert body["run_id"] == run_id
    assert body["workflow_id"] == workflow_id

    # It shows up in the tenant's run listing for this workflow.
    listing = await tenant_client.get(f"{_API}/runs", params={"workflow_id": workflow_id})
    assert listing.status_code == 200, f"{listing.status_code} {listing.text}"
    assert any(r["run_id"] == run_id for r in listing.json()["items"])

    # Inline execution persisted the workflow's step result(s), queryable by run.
    steps = await tenant_client.get(f"{_API}/runs/{run_id}/steps")
    assert steps.status_code == 200, f"{steps.status_code} {steps.text}"
    step_ids = {s["step_id"] for s in steps.json()}
    assert "s1" in step_ids, f"expected step 's1' persisted, got {step_ids}"


async def test_distinct_workflows_run_their_own_graph(
    tenant_client: Any, _inline_runner: None
) -> None:
    """Two API-created workflows must each run their own definition.

    The run engine caches compiled graphs by ``definition.id``. API-created
    definitions must therefore carry a distinct id, or a second workflow would
    reuse the first's compiled graph (and record the wrong steps).
    """
    wf_alpha = await _create_workflow(tenant_client, step_id="alpha")
    wf_beta = await _create_workflow(tenant_client, step_id="beta")

    assert await _trigger_and_step_ids(tenant_client, wf_alpha) == {"alpha"}
    assert await _trigger_and_step_ids(tenant_client, wf_beta) == {"beta"}
