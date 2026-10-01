"""e2e_full: the workflow schedule beat must not read the whole fleet every minute.

``fire_due_workflow_schedules`` runs every 60 seconds and did::

    SELECT id, tenant_id, definition FROM workflows WHERE status = 'published'

— every published workflow of every tenant, **including each one's full JSON
definition**, materialised into one Python list, on every beat tick, on every
beat replica. The "does this workflow have a schedule trigger?" test then ran in
Python over that list. At ten thousand tenants with fifty published workflows
each that is half a million JSONB documents a minute to discover the handful
that are actually due.

The filter belongs in the database (both on-disk trigger shapes are JSONB
containment queries, and a GIN index answers them), and the read has to be
bounded so memory does not scale with the fleet.

Also pinned here: a dedup failure must not fall through into firing. The Redis
SETNX that makes each cron occurrence fire exactly once was wrapped in
``try/except: log`` and then *continued into the dispatch*, so a Redis blip with
two beat replicas running double-fired the occurrence. A missed tick is
recovered 60 seconds later; a duplicate workflow run is not recoverable.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _scan_source() -> str:
    from app.workflow import celery_tasks

    return inspect.getsource(celery_tasks.fire_due_workflow_schedules_async)


@pytest.mark.asyncio(loop_scope="session")
async def test_schedule_scan_filters_in_the_database_not_in_python() -> None:
    source = _scan_source()
    assert "SELECT id, tenant_id, definition FROM workflows" in source, (
        "the schedule scan's SELECT has moved — test is stale"
    )
    from app.workflow.celery_tasks import _SCHEDULE_TRIGGER_PREDICATE

    assert "@>" in _SCHEDULE_TRIGGER_PREDICATE, (
        "the beat still pulls every published workflow in the fleet and filters "
        "for schedule triggers in Python"
    )
    assert "_SCHEDULE_TRIGGER_PREDICATE" in source, (
        "the scan does not apply the schedule-trigger predicate in SQL"
    )
    # Both on-disk trigger shapes must be matched server-side, or the plural
    # (visual-builder) form would silently stop firing.
    assert '"trigger"' in _SCHEDULE_TRIGGER_PREDICATE
    assert '"triggers"' in _SCHEDULE_TRIGGER_PREDICATE


@pytest.mark.asyncio(loop_scope="session")
async def test_schedule_scan_bounds_what_it_loads() -> None:
    source = _scan_source()
    assert "LIMIT" in source, (
        "the schedule scan materialises an unbounded result set; at fleet scale "
        "that is the beat worker's memory, every 60 seconds"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_dedup_failure_does_not_fall_through_into_firing() -> None:
    """A Redis error must skip the occurrence, not fire it unguarded."""
    source = _scan_source()
    dedup = source[source.index("wf:sched:") : source.index("runner.run(")]
    assert "continue" in dedup.split("except", 1)[-1], (
        "a dedup failure still falls through to dispatch — two beat replicas "
        "plus one Redis blip double-fires the occurrence"
    )


async def test_scheduled_workflow_fires_once_and_only_when_due(
    app: Any, tenant_client: Any
) -> None:
    """Behavioural: a due schedule fires exactly one run; a non-due one fires none."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.workflow.celery_tasks import fire_due_workflow_schedules_async

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    # An every-minute cron needs a plan whose schedule floor is 60 s.
    await app.state.tenant_service.update_plan(tenant_id, "professional")

    async def _publish(trigger: dict[str, Any]) -> str:
        """Create and publish through the real API, so the workflows →
        workflow_definitions bridge runs and the run engine can resolve it."""
        name = f"sched-{uuid.uuid4().hex[:8]}"
        resp = await tenant_client.post(
            "/api/v1/workflows",
            json={
                "name": name,
                "description": "schedule scan audit",
                "definition": {
                    "name": name,
                    **trigger,
                    "steps": [{"id": "s1", "type": "transform", "input": {"x": 1}}],
                },
            },
        )
        assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
        workflow_id = str(resp.json()["id"])
        pub = await tenant_client.post(f"/api/v1/workflows/{workflow_id}/publish")
        assert pub.status_code == 200, f"publish failed: {pub.status_code} {pub.text}"
        return workflow_id

    # Due every minute (DSL singular shape) and a yearly one that is not due.
    due_id = await _publish({"trigger": {"type": "schedule", "schedule": {"cron": "* * * * *"}}})
    not_due_id = await _publish(
        {"trigger": {"type": "schedule", "schedule": {"cron": "0 0 1 1 *", "timezone": "UTC"}}}
    )
    # A published workflow with no schedule trigger must not even be scanned.
    no_schedule_id = await _publish({"trigger": {"type": "webhook"}})

    async def _runs(workflow_id: str) -> int:
        async with (
            app.state.db_session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            return int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM workflow_runs "
                            "WHERE workflow_id = CAST(:wid AS uuid) "
                            "AND tenant_id = CAST(:tid AS uuid)"
                        ),
                        {"wid": workflow_id, "tid": tenant_id},
                    )
                ).scalar_one()
            )

    result = await fire_due_workflow_schedules_async()
    assert result["fired"] >= 1, f"a due schedule did not fire: {result}"

    assert await _runs(due_id) == 1
    assert await _runs(not_due_id) == 0, "a schedule that is not due fired anyway"
    assert await _runs(no_schedule_id) == 0

    # Second tick, same occurrence: the Redis SETNX must suppress it.
    await fire_due_workflow_schedules_async()
    assert await _runs(due_id) == 1, "the same cron occurrence fired twice"
