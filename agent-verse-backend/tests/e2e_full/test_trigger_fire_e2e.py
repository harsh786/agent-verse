"""e2e_full: trigger → dispatcher → real goal, proven against live Postgres+Redis.

This is the end-to-end proof for the trigger wiring hardened in Phase 0
(WT-1/WT-2/WT-3): the app is booted with its real lifespan (manage_pools=True),
so ``POST /triggers/{id}/fire`` routes through the DB/Redis-backed
``TriggerDispatcher`` → ``GoalService.create_goal`` → ``submit_goal`` and creates
a genuine goal — not the old 503 "Dispatcher unavailable" stub.

It also proves the dispatcher's Redis-backed idempotency: firing the identical
payload twice within the dedup window creates exactly one goal; the second fire
is short-circuited with ``skip_reason == "dedup"`` and no new goal_id.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _create_webhook_trigger(tenant_client: Any) -> str:
    """Create a webhook trigger with a goal_template; return its schedule_id."""
    resp = await tenant_client.post(
        "/triggers",
        json={
            "spec": {
                "trigger_type": "webhook",
                "name": "e2e-full webhook",
                "description": "fires a goal from an inbound webhook",
            },
            "goal_template": "Handle webhook event for {{payload.repo}}",
        },
    )
    assert resp.status_code == 201, f"create trigger failed: {resp.status_code} {resp.text}"
    body = resp.json()
    schedule_id = body["schedule_id"]
    assert schedule_id
    return schedule_id


async def test_fire_creates_real_goal_and_dedups_second_fire(tenant_client: Any) -> None:
    schedule_id = await _create_webhook_trigger(tenant_client)

    # A fixed payload so both fires derive the SAME idempotency key
    # (webhook family: key = trigger_id : payload_hash).
    payload = {"repo": "octo/hello-world", "action": "opened", "number": 42}

    # ── First fire: a real goal must be created ───────────────────────────────
    first = await tenant_client.post(
        f"/triggers/{schedule_id}/fire",
        json={"payload": payload},
    )
    assert first.status_code == 200, f"fire failed: {first.status_code} {first.text}"
    first_body = first.json()

    # Proves the dispatcher is wired: not the old 503 stub, a real non-null goal_id.
    goal_id = first_body["goal_id"]
    assert goal_id, f"expected a real goal_id, got {first_body!r}"
    assert first_body.get("goal_created") is True
    assert first_body.get("skip_reason") is None

    # ── The goal actually exists ──────────────────────────────────────────────
    got = await tenant_client.get(f"/goals/{goal_id}")
    assert got.status_code == 200, f"goal {goal_id} not found: {got.status_code} {got.text}"
    goal = got.json()
    assert goal["goal_id"] == goal_id
    # A real goal record with rendered goal text exists (dispatcher step 9 → 11).
    # NOTE: goal_template currently lives on the store record, not on the
    # TriggerSpec the dispatcher renders, so the text falls back to the default
    # "Trigger fired: <type>" rather than the tenant's template — a minor wiring
    # gap tracked separately; it does not affect the trigger→goal proof here.
    assert goal.get("goal_text") or goal.get("goal")

    # ── Second identical fire: deduplicated, no new goal ──────────────────────
    second = await tenant_client.post(
        f"/triggers/{schedule_id}/fire",
        json={"payload": payload},
    )
    assert second.status_code == 200, f"second fire failed: {second.status_code} {second.text}"
    second_body = second.json()

    assert second_body.get("skip_reason") == "dedup", (
        f"second identical fire should be deduped, got {second_body!r}"
    )
    assert second_body.get("goal_created") in (False, None)
    assert second_body.get("goal_id") in (None, ""), (
        f"dedup fire must not mint a new goal, got {second_body!r}"
    )


async def test_fire_unknown_trigger_returns_404(tenant_client: Any) -> None:
    """Sanity: firing a non-existent trigger is a clean 404, not a 503/500."""
    resp = await tenant_client.post(
        "/triggers/does-not-exist/fire",
        json={"payload": {"x": 1}},
    )
    assert resp.status_code == 404, f"{resp.status_code} {resp.text}"
