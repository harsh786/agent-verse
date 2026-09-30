"""e2e_full (WS-4): a scheduled (cron) trigger fire is dispatcher-governed.

Proves, against live Postgres + Redis, that the ``TriggerDispatcher``'s
Redis-backed idempotency governs a *scheduled* / replayed fire — the exact
protection that stops a schedule that fires twice for the same tick (a beat
replay, a retried ``run_scheduled_goal``, an at-least-once delivery) from
double-running the tenant's goal.

For time-based trigger families (``cron`` / ``interval`` / ``once`` …) the
dispatcher derives its idempotency key from ``trigger_id : scheduled_fire_time``
(see ``app/triggers/dedup.derive_idempotency_key``) — deliberately independent
of the payload. So two fires of the same cron trigger within the dedup window
collide on the same key regardless of payload: the first mints a real goal, the
second is short-circuited with ``skip_reason == "dedup"`` and no new goal.

This is distinct from ``test_trigger_fire_e2e`` (a *webhook* trigger, whose key
is a payload hash): here we deliberately vary the payload between the two fires
to show the governance is keyed on the schedule tick, not the payload.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _create_cron_trigger(tenant_client: Any) -> str:
    """Create a cron (time-based / scheduled) trigger; return its schedule_id."""
    resp = await tenant_client.post(
        "/triggers",
        json={
            "spec": {
                "trigger_type": "cron",
                "name": "ws4-scheduled",
                "description": "a scheduled cron trigger",
                # Every 15 min: the free plan's minimum schedule interval
                # (TRG-16); the shared e2e_full session tenant is free-plan.
                "cron_expression": "*/15 * * * *",
                "timezone": "UTC",
            },
            "goal_template": "Scheduled maintenance sweep",
        },
    )
    assert resp.status_code == 201, f"create cron trigger failed: {resp.status_code} {resp.text}"
    schedule_id = resp.json()["schedule_id"]
    assert schedule_id
    return schedule_id


async def test_scheduled_fire_dedups_on_replay(tenant_client: Any) -> None:
    schedule_id = await _create_cron_trigger(tenant_client)

    # ── First scheduled fire: a real goal is created ──────────────────────────
    tick = "2026-09-28T09:00:00+00:00"
    first = await tenant_client.post(
        f"/triggers/{schedule_id}/fire",
        json={"payload": {"tick": "A", "n": 1}, "scheduled_fire_time": tick},
    )
    assert first.status_code == 200, f"first fire failed: {first.status_code} {first.text}"
    first_body = first.json()
    goal_id = first_body["goal_id"]
    assert goal_id, f"first scheduled fire must mint a real goal, got {first_body!r}"
    assert first_body.get("goal_created") is True
    assert first_body.get("skip_reason") is None

    # The goal genuinely exists (dispatcher → GoalService.create_goal).
    got = await tenant_client.get(f"/goals/{goal_id}")
    assert got.status_code == 200, f"goal {goal_id} not found: {got.status_code} {got.text}"

    # ── Replayed scheduled fire (DIFFERENT payload): deduplicated ─────────────
    # A cron trigger's idempotency key is trigger_id:scheduled_fire_time and does
    # NOT include the payload, so even a different payload collides on the same
    # tick and is governed away — no second goal.
    second = await tenant_client.post(
        f"/triggers/{schedule_id}/fire",
        json={"payload": {"tick": "B", "n": 2}, "scheduled_fire_time": tick},
    )
    assert second.status_code == 200, f"second fire failed: {second.status_code} {second.text}"
    second_body = second.json()
    assert second_body.get("skip_reason") == "dedup", (
        f"replayed scheduled fire must be deduped, got {second_body!r}"
    )
    assert second_body.get("goal_created") in (False, None)
    assert second_body.get("goal_id") in (None, ""), (
        f"dedup fire must not mint a new goal, got {second_body!r}"
    )


async def test_independent_manual_fires_are_not_deduplicated(tenant_client: Any) -> None:
    """Regression: with no fire time the key was the constant 'now', so a
    trigger's manual fire worked exactly once and every later one was a 'dup'."""
    schedule_id = await _create_cron_trigger(tenant_client)
    bodies = []
    for n in (1, 2):
        r = await tenant_client.post(f"/triggers/{schedule_id}/fire", json={"payload": {"n": n}})
        assert r.status_code == 200, r.text
        bodies.append(r.json())
    assert bodies[0].get("goal_created") is True, bodies
    # The second fire is NOT a trigger-level duplicate. It either creates its own
    # goal or — while the identical first goal is still in flight — is folded
    # into it and says so honestly (never reported as a newly created goal).
    second = bodies[1]
    assert second.get("skip_reason") != "dedup", bodies
    if second.get("goal_id") == bodies[0]["goal_id"]:
        assert second.get("goal_created") is False
        assert second.get("skip_reason") == "goal_in_progress"
    else:
        assert second.get("goal_created") is True
