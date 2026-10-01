"""e2e_full (WF-APPROVAL-WORKFLOW-ID): an approval raised by a workflow gate
carries its workflow_id, run_id and step_id everywhere — the DB row, the API
(list + detail) and the inbox SSE payload — and a decided approval's status is
``approved`` / ``rejected`` (not the generic ``decided``).

Before: ``HITLStepNode`` never passed the run's workflow_id, so every workflow
approval had ``workflow_id=''`` and the inbox could not link it to its
workflow; the SSE event carried only request_id + priority.

Real app + Postgres + Redis + out-of-process worker (where the gate runs).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.e2e_full._wf_worker import (
    API,
    create_workflow,
    pending_approval_for,
    poll_run,
    workflow_worker,
)

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def celery_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    with workflow_worker(tmp_path_factory.mktemp("idsworker"), name="wfidse2e") as w:
        yield w


@pytest.fixture
def _fast_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workflow import router_hitl

    monkeypatch.setattr(router_hitl, "_STREAM_POLL_SECONDS", 0.05)
    monkeypatch.setattr(router_hitl, "_STREAM_MAX_POLLS", 3)


def _gate_workflow(assignee: str) -> dict[str, Any]:
    return {
        "name": "Approval ids",
        "steps": [
            {
                "id": "manager_gate",
                "type": "hitl",
                "assignee": {"strategy": "specific", "specific_user": assignee},
                "actions": [{"id": "approve"}, {"id": "reject"}],
            }
        ],
    }


async def _db_row(app: Any, request_id: str, tenant_id: str) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        app.state.db_session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT workflow_id, run_id, step_id FROM workflow_approvals "
                    "WHERE request_id = :r AND tenant_id = CAST(:t AS uuid)"
                ),
                {"r": request_id, "t": tenant_id},
            )
        ).mappings().first()
    assert row is not None
    return dict(row)


@pytest.mark.parametrize(("action", "status"), [("approve", "approved"), ("reject", "rejected")])
async def test_workflow_approval_carries_ids_everywhere(
    app: Any,
    tenant_client: Any,
    celery_worker: dict[str, Any],
    _fast_stream: None,
    action: str,
    status: str,
) -> None:
    from httpx import ASGITransport, AsyncClient

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    keys = (await tenant_client.get("/tenants/me/keys")).json()
    keys = keys.get("keys", keys) if isinstance(keys, dict) else keys
    # Assigned to the caller, so the (role-filtered) inbox stream shows it.
    wid = await create_workflow(
        tenant_client, _gate_workflow(str(keys[0]["key_id"])), name=f"ids-{uuid.uuid4().hex[:8]}"
    )
    trig = await tenant_client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {}, "dry_run": False}
    )
    assert trig.status_code == 202, trig.text
    run_id = str(trig.json()["run_id"])
    await poll_run(tenant_client, run_id, {"waiting_hitl"})

    item = await pending_approval_for(tenant_client, run_id)
    expected = {"workflow_id": wid, "run_id": run_id, "step_id": "manager_gate"}
    assert {k: item.get(k) for k in expected} == expected, item

    detail = (await tenant_client.get(f"{API}/approvals/{item['request_id']}")).json()
    assert {k: detail.get(k) for k in expected} == expected, detail

    row = await _db_row(app, str(item["request_id"]), tenant_id)
    assert row == expected

    token = (await tenant_client.get("/tenants/stream-token")).json()["token"]
    events: list[dict[str, Any]] = []
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e-full") as es,
        es.stream("GET", f"{API}/approvals/stream", params={"token": token}) as resp,
    ):
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data:") and "[DONE]" not in line:
                events.append(json.loads(line[5:].strip()))
    mine = [e for e in events if e.get("request_id") == item["request_id"]]
    assert mine, events
    assert {k: mine[0].get(k) for k in expected} == expected, mine[0]
    assert mine[0].get("kind") == "workflow"

    decided = await tenant_client.post(
        f"{API}/approvals/{item['request_id']}/decide", json={"action": action}
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == status
    assert decided.json()["workflow_id"] == wid
