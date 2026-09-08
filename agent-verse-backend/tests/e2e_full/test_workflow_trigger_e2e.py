"""e2e_full: workflow create → trigger.

Documents a verified architectural disconnect in the workflow subsystem, found
by driving the wired path end-to-end:

* ``POST /api/v1/workflows`` (WorkflowService → ``_WorkflowStore`` → the ``Workflow``
  ORM) persists the DSL in the **legacy ``workflows`` table** (Text id, JSONB
  ``definition``).
* The run engine (``WorkflowRunner`` + ``PostgresWorkflowRunStore``, migration
  0108) is built entirely around the **``workflow_definitions`` table** (uuid id):
  ``workflow_runs.workflow_id`` has a FK to ``workflow_definitions(id)``.
* **Nothing populates ``workflow_definitions``.** So triggering an API-created
  workflow fails — first the definition lookup misses, and even past that,
  inserting the run violates ``workflow_runs_workflow_id_fkey``.

Net: workflow triggering is broken for any workflow created through the public
API. The fix is a reconciliation of the two persistence systems (a migration +
WorkflowService refactor, or a bridge that writes both) and is tracked as a
dedicated task — too large to land safely here. These tests are marked xfail so
the gap is recorded, not hidden, and will flip to pass once reconciled.

The create path itself IS wired and works (asserted below, not xfail).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"


async def _create_workflow(tenant_client: Any) -> str:
    body = {
        "name": f"e2e-wf-{uuid.uuid4().hex[:8]}",
        "description": "workflow trigger e2e",
        "definition": {"name": "E2E Trigger WF", "steps": [{"id": "s1", "type": "tool"}]},
    }
    resp = await tenant_client.post(f"{_API}/workflows", json=body)
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def test_workflow_create_and_get_is_wired(tenant_client: Any) -> None:
    """The create/read path is genuinely wired (DB-backed, RLS-scoped)."""
    workflow_id = await _create_workflow(tenant_client)
    got = await tenant_client.get(f"{_API}/workflows/{workflow_id}")
    assert got.status_code == 200, f"{got.status_code} {got.text}"
    assert got.json()["id"] == workflow_id


@pytest.mark.xfail(
    reason="Workflow persistence disconnect: API writes `workflows`, run engine "
    "FK-references `workflow_definitions` (never populated) → trigger fails a "
    "workflow_runs FK violation. Tracked for reconciliation.",
    strict=True,
)
async def test_trigger_creates_persisted_run(tenant_client: Any) -> None:
    workflow_id = await _create_workflow(tenant_client)
    trig = await tenant_client.post(
        f"{_API}/workflows/{workflow_id}/trigger",
        json={"inputs": {}, "dry_run": True},
    )
    assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
    run_id = trig.json()["run_id"]
    got = await tenant_client.get(f"{_API}/runs/{run_id}")
    assert got.status_code == 200
    assert got.json()["workflow_id"] == workflow_id
