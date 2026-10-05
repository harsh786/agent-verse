"""e2e_full: a failing step next to parallel branches pauses the run in a real worker.

User report: a digest workflow (set -> http || web_search -> llm + code -> gate)
"hung". On the real Celery worker path the failing branch marked the run PAUSED,
then both next-superstep steps wrote ``status`` concurrently and LangGraph raised
InvalidUpdateError, so the run died with a framework message instead of pausing
on the step that failed. This drives the same shape through a genuine worker
subprocess with no network dependency: the failing branch calls a tool no
connector exposes.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from tests.e2e_full.test_workflow_run_e2e import celery_worker  # noqa: F401

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"
_SETTLED = {"complete", "failed", "cancelled", "timed_out", "paused", "waiting_hitl"}


async def test_parallel_branch_failure_pauses_on_failing_step(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    definition = {
        "name": "parallel failure",
        "steps": [
            {"id": "set_company", "type": "set_variable", "var_name": "company",
             "var_value": "Tesla"},
            {"id": "ok_branch", "type": "transform", "input": {"v": "{{vars.company}}"},
             "depends_on": ["set_company"]},
            # A read-only name: a write_high tool step would wait for approval (OI-2).
            {"id": "bad_branch", "type": "tool", "tool": "search_tool_that_no_connector_has",
             "input": {"q": "x"}, "depends_on": ["set_company"]},
            {"id": "join", "type": "transform", "input": {"v": 1},
             "depends_on": ["ok_branch", "bad_branch"]},
            {"id": "after_ok", "type": "transform", "input": {"v": 2},
             "depends_on": ["ok_branch"]},
        ],
    }
    resp = await tenant_client.post(
        f"{_API}/workflows",
        json={"name": f"par-fail-{uuid.uuid4().hex[:8]}", "definition": definition},
    )
    assert resp.status_code == 201, resp.text
    trig = await tenant_client.post(
        f"{_API}/workflows/{resp.json()['id']}/trigger", json={"inputs": {}}
    )
    assert trig.status_code == 202, trig.text
    run_id = trig.json()["run_id"]

    run: dict[str, Any] = {}
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        r = await tenant_client.get(f"{_API}/runs/{run_id}")
        if r.status_code == 200:
            run = r.json()
            if str(run.get("status")) in _SETTLED:
                break
        await asyncio.sleep(1.0)

    assert run.get("status") == "paused", f"expected paused, got {run!r}"
    err = str(run.get("error") or "")
    assert "InvalidUpdateError" not in err and "Can receive only one value" not in err, err
    assert "search_tool_that_no_connector_has" in err, err
