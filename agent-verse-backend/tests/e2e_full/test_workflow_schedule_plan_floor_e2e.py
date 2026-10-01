"""e2e_full (WF-PLAN-FLOOR): the plan's minimum schedule interval (Settings
``SCHEDULE_MIN_INTERVAL_<PLAN>_S``; free = 900 s) binds WORKFLOW schedules too.

Before: a free-plan tenant created and published a workflow with cron
``* * * * *`` (publish only checked that a cron existed) and the beat fired it
every minute — /schedules and /triggers enforced the floor, workflows did not.
Now creation, update and publish refuse it with the reason (422), and the
beat re-checks at fire time (a definition stored before the check, or a plan
downgrade, never fires faster than the plan allows).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.e2e_full._wf_worker import API

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_STEPS = [{"id": "s1", "type": "transform", "input": {"x": 1}}]


def _definition(cron: str) -> dict[str, Any]:
    return {
        "name": "floor",
        "trigger": {"type": "schedule", "schedule": {"cron": cron}},
        "steps": _STEPS,
    }


async def _create(client: Any, cron: str) -> Any:
    return await client.post(
        f"{API}/workflows",
        json={"name": f"floor-{uuid.uuid4().hex[:8]}", "definition": _definition(cron)},
    )


async def test_free_plan_refuses_a_sub_floor_cron_on_create_update_publish(
    tenant_client: Any,
) -> None:
    refused = await _create(tenant_client, "* * * * *")
    assert refused.status_code == 422, refused.text
    assert "free plan" in refused.text.lower()

    ok = await _create(tenant_client, "*/15 * * * *")  # exactly the 900 s floor
    assert ok.status_code == 201, ok.text
    wid = ok.json()["id"]

    patch = await tenant_client.patch(
        f"{API}/workflows/{wid}", json={"definition": _definition("*/5 * * * *")}
    )
    assert patch.status_code == 422, patch.text
    assert "free plan" in patch.text.lower()

    pub = await tenant_client.post(f"{API}/workflows/{wid}/publish")
    assert pub.status_code == 200, pub.text


async def _workflow_runs(app: Any, tenant_id: str, workflow_id: str) -> int:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        app.state.db_session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        return int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM workflow_runs WHERE workflow_id = CAST(:w AS uuid) "
                        "AND tenant_id = CAST(:t AS uuid)"
                    ),
                    {"w": workflow_id, "t": tenant_id},
                )
            ).scalar_one()
        )


async def _force_definition(app: Any, tenant_id: str, workflow_id: str, cron: str) -> None:
    """Store a sub-floor cron directly (a definition saved before this check
    existed, or one that was valid on the plan the tenant later left)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        app.state.db_session_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        await session.execute(
            text(
                "UPDATE workflows SET definition = CAST(:d AS jsonb) "
                "WHERE id = :w AND tenant_id = :t"
            ),
            {"d": json.dumps(_definition(cron)), "w": workflow_id, "t": tenant_id},
        )


async def test_beat_refuses_to_fire_below_the_floor_and_fires_on_a_paid_plan(
    app: Any, tenant_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workflow import celery_tasks as ct

    tenant_id = str((await tenant_client.get("/tenants/me")).json()["tenant_id"])
    created = await _create(tenant_client, "0 9 * * 1")
    assert created.status_code == 201, created.text
    wid = created.json()["id"]
    assert (await tenant_client.post(f"{API}/workflows/{wid}/publish")).status_code == 200
    await _force_definition(app, tenant_id, wid, "* * * * *")

    now = datetime.now(UTC)
    monkeypatch.setattr(ct, "_get_runner", lambda: app.state.workflow_runner)
    monkeypatch.setattr(
        ct, "_cron_bounds", lambda *_a, **_k: (now - timedelta(seconds=5), now + timedelta(seconds=55))
    )
    await ct.fire_due_workflow_schedules_async()
    assert await _workflow_runs(app, tenant_id, wid) == 0, "free plan fired every minute"

    # The professional floor (60 s) allows it: the very same occurrence fires.
    await app.state.tenant_service.update_plan(tenant_id, "professional")
    await ct.fire_due_workflow_schedules_async()
    assert await _workflow_runs(app, tenant_id, wid) == 1

    # And on that plan the API accepts the every-minute cron.
    accepted = await _create(tenant_client, "* * * * *")
    assert accepted.status_code == 201, accepted.text
