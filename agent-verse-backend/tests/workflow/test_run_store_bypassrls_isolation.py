"""RUNS-TENANT: workflow stores isolate tenants even on a BYPASSRLS connection.

The local Docker stack connected as ``agentverse`` — a SUPERUSER, which bypasses
row-level security on every table — and ``GET /api/v1/runs`` returned every
tenant's runs, because ``PostgresWorkflowRunStore`` relied ONLY on RLS (the
``app.tenant_id`` GUC). Every tenant-scoped query now also carries an explicit
``tenant_id`` predicate. These tests connect as the testcontainer's SUPERUSER
(RLS is not enforced at all) and assert tenant B never sees, changes or counts
tenant A's rows.

Run with:
    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/workflow/test_run_store_bypassrls_isolation.py -q
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workflow.approval_store import PostgresWorkflowApprovalStore
from app.workflow.hitl_extension import WorkflowHITLRequest
from app.workflow.run_store import PostgresWorkflowRunStore
from app.workflow.state import StepStatus, WorkflowRunStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def _seed_tenant(
    factory: Any, store: PostgresWorkflowRunStore, approvals: PostgresWorkflowApprovalStore
) -> dict[str, str]:
    tid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO workflow_definitions "
                "(id, tenant_id, name, slug, definition_json, status) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), 'wf', :slug, "
                " CAST(:dj AS jsonb), 'published')"
            ),
            {"id": wid, "tid": tid, "slug": f"wf-{wid}", "dj": json.dumps({"steps": []})},
        )
        await s.execute(
            text(
                "INSERT INTO workflow_definition_versions "
                "(workflow_id, tenant_id, version, definition_yaml, definition_json) "
                "VALUES (CAST(:wid AS uuid), CAST(:tid AS uuid), '1.0.0', 'x: 1', "
                " CAST('{}' AS jsonb))"
            ),
            {"wid": wid, "tid": tid},
        )
    run_id = str(uuid.uuid4())
    await store.create(run_id=run_id, workflow_id=wid, tenant_id=tid)
    await store.update_status(run_id, WorkflowRunStatus.RUNNING, tenant_id=tid)
    await store.record_step_start(run_id=run_id, tenant_id=tid, step_id="s1", step_type="t")
    await store.record_step_finish(
        run_id=run_id, tenant_id=tid, step_id="s1", status=StepStatus.COMPLETE, output={"a": 1}
    )
    await store.update_status(run_id, WorkflowRunStatus.COMPLETE, tenant_id=tid)
    await store.set_timer_wait(tid, run_id, "s1", datetime.now(UTC) + timedelta(hours=1))
    perm = await store.add_permission(
        tid, wid, subject_type="user", subject_id="u1", permission="editor"
    )
    event_id = await store.record_webhook_failure(
        tenant_id=tid, workflow_id=wid, token_fingerprint="fp", payload={}, error="x"
    )
    req = WorkflowHITLRequest(
        run_id=run_id, workflow_id=wid, tenant_id=tid, step_id="s1", assigned_to="u1"
    )
    await approvals.save(req)
    return {
        "tid": tid,
        "wid": wid,
        "run": run_id,
        "perm": perm["id"],
        "event": event_id,
        "req": req.request_id,
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def two_tenants(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    """Two seeded tenants on a SUPERUSER (RLS-bypassing) connection."""
    engine = create_async_engine(pg_url, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        bypass = (
            await s.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).scalar_one()
    assert bypass, "this test must run on a role that bypasses RLS"
    store = PostgresWorkflowRunStore(factory, system_db_factory=factory)
    approvals = PostgresWorkflowApprovalStore(factory)
    a = await _seed_tenant(factory, store, approvals)
    b = await _seed_tenant(factory, store, approvals)
    yield {"a": a, "b": b, "store": store, "approvals": approvals}
    await engine.dispose()


async def test_run_reads_never_cross_tenants(two_tenants: dict[str, Any]) -> None:
    store: PostgresWorkflowRunStore = two_tenants["store"]
    a, b = two_tenants["a"], two_tenants["b"]

    items, total = await store.list(b["tid"])
    assert {r["run_id"] for r in items} == {b["run"]}
    assert total == 1
    items, total = await store.list(b["tid"], workflow_id=a["wid"])
    assert items == [] and total == 0

    assert await store.get(b["tid"], a["run"]) is None
    assert await store.get(b["tid"], b["run"]) is not None
    assert await store.get_status(b["tid"], a["run"]) is None
    assert await store.get_workflow_id(a["run"], b["tid"]) == ""
    with pytest.raises(KeyError):
        await store.get_definition(a["wid"], b["tid"])

    assert await store.list_step_results(b["tid"], a["run"]) == []
    assert await store.get_step_result(b["tid"], a["run"], "s1") is None
    assert await store.get_timer_wait(b["tid"], a["run"], "s1") is None
    assert await store.get_event_delivery(b["tid"], a["run"], "s1") is None


async def test_versions_permissions_stats_webhooks_never_cross_tenants(
    two_tenants: dict[str, Any],
) -> None:
    store: PostgresWorkflowRunStore = two_tenants["store"]
    a, b = two_tenants["a"], two_tenants["b"]

    assert await store.list_versions(b["tid"], a["wid"]) == []
    assert await store.get_definition_version(b["tid"], a["wid"], "1.0.0") is None
    assert await store.get_permissions(b["tid"], a["wid"]) == []
    assert await store.get_publish_approval(b["tid"], a["wid"]) is None
    assert await store.get_webhook_token_version(b["tid"], a["wid"]) == 0

    stats = await store.workflow_run_stats(b["tid"], a["wid"])
    assert stats["total"] == 0
    agg = await store.aggregate_run_stats(b["tid"])
    assert agg["total"] == 1

    events, total = await store.list_webhook_events(b["tid"], a["wid"])
    assert events == [] and total == 0


async def test_run_writes_never_touch_other_tenants(two_tenants: dict[str, Any]) -> None:
    store: PostgresWorkflowRunStore = two_tenants["store"]
    a, b = two_tenants["a"], two_tenants["b"]

    assert await store.update_status(a["run"], "cancelled", tenant_id=b["tid"]) is False
    assert (
        await store.record_step_finish(
            run_id=a["run"], tenant_id=b["tid"], step_id="s1", status="failed"
        )
        is False
    )
    assert await store.copy_completed_step_results(b["tid"], a["run"], b["run"]) == 0
    assert await store.remove_permission(b["tid"], a["wid"], a["perm"]) is False
    assert await store.set_requires_publish_approval(b["tid"], a["wid"], True) is False
    assert await store.set_publish_submission(b["tid"], a["wid"], {"v": "2"}) is False
    assert (
        await store.record_publish_approval(b["tid"], a["wid"], approved_by="x", note="n")
        is False
    )
    with pytest.raises(KeyError):
        await store.rotate_webhook_token(b["tid"], a["wid"])
    assert (
        await store.mark_webhook_attempt(tenant_id=b["tid"], event_id=a["event"], error="e")
        is None
    )
    await store.mark_stuck_redispatched(b["tid"], a["run"])
    await store.release_timer_claim(b["tid"], a["run"])
    await store.clear_event_wait(b["tid"], a["run"], "s1")
    await store.set_timer_wait(b["tid"], a["run"], "evil", datetime.now(UTC))
    await store.register_event_wait(b["tid"], a["run"], "evil", "ch", datetime.now(UTC))
    # A step row for another tenant's run must not be created.
    with pytest.raises(KeyError):
        await store.record_step_start(
            run_id=a["run"], tenant_id=b["tid"], step_id="x", step_type="t"
        )

    # Tenant A's data is untouched.
    run = await store.get(a["tid"], a["run"])
    assert run is not None and run["status"] == "complete"
    assert "stuck_redispatched_at" not in run["run_metadata"]
    assert "evil" not in (run["run_metadata"].get("timer_waits") or {})
    assert "evil" not in (run["run_metadata"].get("event_waits") or {})
    steps = await store.list_step_results(a["tid"], a["run"])
    assert [s["step_id"] for s in steps] == ["s1"]
    assert steps[0]["status"] == "complete"
    assert len(await store.get_permissions(a["tid"], a["wid"])) == 1
    approval = await store.get_publish_approval(a["tid"], a["wid"])
    assert approval is not None and approval["requires_publish_approval"] is False
    assert approval["submission"] is None and approval["approved_by"] is None
    assert await store.get_webhook_token_version(a["tid"], a["wid"]) == 0
    events, _ = await store.list_webhook_events(a["tid"], a["wid"])
    assert events[0]["attempts"] == 0


async def test_approvals_never_cross_tenants(two_tenants: dict[str, Any]) -> None:
    approvals: PostgresWorkflowApprovalStore = two_tenants["approvals"]
    a, b = two_tenants["a"], two_tenants["b"]

    assert await approvals.get(a["req"], b["tid"]) is None
    pending, total = await approvals.list_pending(b["tid"])
    assert [r.request_id for r in pending] == [b["req"]] and total == 1
    stats = await approvals.get_stats(b["tid"])
    assert stats["total_requests"] == 1
    assert await approvals.list_by_run(b["tid"], a["run"]) == []
    load = await approvals.assignee_load(b["tid"], ["u1"])
    assert load["u1"][0] == 1

    # Neither a decision nor an upsert under tenant B can rewrite A's approval.
    hijack = WorkflowHITLRequest(
        request_id=a["req"], run_id=a["run"], tenant_id=b["tid"], status="approved"
    )
    assert await approvals.decide_if_pending(hijack) is False
    with pytest.raises(PermissionError):
        await approvals.save(hijack)
    still = await approvals.get(a["req"], a["tid"])
    assert still is not None and still.status == "pending" and still.tenant_id == a["tid"]
