"""e2e_full: trigger foundation proven against the live wired app + real Postgres.

Exercises the production-hardening foundation (spec contract, enum reconciliation,
goal binding, per-type validation, metadata serialization) through the fully
booted app (manage_pools=True) over the real HTTP surface, so the whole stack —
request → validation → DB-backed ScheduleStore → serialize → list — is proven,
not just the in-memory unit path.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# One valid spec per core, dispatch-supported family.
_CORE_FAMILY_SPECS = [
    {"trigger_type": "cron", "cron_expression": "0 9 * * 1-5", "description": "weekday 9am"},
    {"trigger_type": "interval", "interval_seconds": 3600, "description": "hourly"},
    {"trigger_type": "once", "fire_at_iso": "2035-01-01T00:00:00Z", "description": "new year"},
    {"trigger_type": "webhook", "description": "inbound webhook"},
    {"trigger_type": "api_poll", "poll_url": "https://example.com/status", "description": "poll"},
]


async def test_core_families_create_and_list_with_metadata(tenant_client: Any) -> None:
    """Every core family creates through the wired app and lists back with its
    created_at timestamp and round-tripped spec."""
    created: dict[str, str] = {}
    for spec in _CORE_FAMILY_SPECS:
        resp = await tenant_client.post(
            "/triggers",
            json={"spec": spec, "goal_template": f"handle {spec['trigger_type']}"},
        )
        assert resp.status_code == 201, f"{spec['trigger_type']}: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["created_at"], "created_at must be serialized by the wired app"
        created[spec["trigger_type"]] = body["schedule_id"]

    listed = await tenant_client.get("/triggers")
    assert listed.status_code == 200
    by_id = {t["schedule_id"]: t for t in listed.json()}
    for ttype, sid in created.items():
        assert sid in by_id, f"{ttype} missing from list"
        assert by_id[sid]["spec"]["trigger_type"] == ttype
        assert by_id[sid]["created_at"]


async def test_advanced_options_round_trip_through_wired_app(tenant_client: Any) -> None:
    """Advanced production controls persist end-to-end (not dropped in serialization)."""
    resp = await tenant_client.post(
        "/triggers",
        json={
            "spec": {
                "trigger_type": "cron",
                "cron_expression": "*/15 * * * *",
                "priority": "high",
                "max_firings_per_hour": 10,
                "expires_at_iso": "2035-06-01T00:00:00Z",
                "condition": "payload.env == 'prod'",
                "tags": ["ops", "billing"],
                "simulation_mode": True,
            },
            "goal_template": "advanced",
        },
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["schedule_id"]
    got = await tenant_client.get(f"/triggers/{sid}")
    assert got.status_code == 200
    spec = got.json()["spec"]
    assert spec["priority"] == "high"
    assert spec["max_firings_per_hour"] == 10
    assert spec["condition"] == "payload.env == 'prod'"
    assert spec["tags"] == ["ops", "billing"]
    assert spec["simulation_mode"] is True


async def test_misconfigured_spec_rejected_by_wired_app(tenant_client: Any) -> None:
    """A cron with no expression is rejected with 422 through the full stack."""
    resp = await tenant_client.post(
        "/triggers",
        json={"spec": {"trigger_type": "cron"}, "goal_template": "x"},
    )
    assert resp.status_code == 422, resp.text
    assert "cron_expression" in resp.json()["detail"]


async def test_unknown_type_rejected_by_wired_app(tenant_client: Any) -> None:
    """A UI-only/legacy type name (pre-reconciliation) is rejected, not silently kept."""
    resp = await tenant_client.post(
        "/triggers",
        json={"spec": {"trigger_type": "custom_webhook"}, "goal_template": "x"},
    )
    assert resp.status_code == 422, resp.text


async def test_goal_id_binding_round_trips_through_wired_app(tenant_client: Any) -> None:
    """A trigger bound to a concrete goal_id (no template/agent) persists that id."""
    resp = await tenant_client.post(
        "/triggers",
        json={
            "spec": {"trigger_type": "interval", "interval_seconds": 900},
            "goal_id": "goal-bound-e2e",
        },
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["schedule_id"]
    got = await tenant_client.get(f"/triggers/{sid}")
    assert got.json()["goal_id"] == "goal-bound-e2e"


async def test_goal_template_is_rendered_on_fire(tenant_client: Any) -> None:
    """DEFECT CHECK: firing a trigger must render the tenant's goal_template with
    payload interpolation, not fall back to the generic 'Trigger fired: <type>'."""
    resp = await tenant_client.post(
        "/triggers",
        json={
            "spec": {"trigger_type": "webhook", "description": "deploy hook"},
            "goal_template": "Deploy {{payload.service}} to {{payload.env}}",
        },
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["schedule_id"]

    fired = await tenant_client.post(
        f"/triggers/{sid}/fire",
        json={"payload": {"service": "billing-api", "env": "prod"}},
    )
    assert fired.status_code == 200, fired.text
    goal_id = fired.json()["goal_id"]
    assert goal_id

    got = await tenant_client.get(f"/goals/{goal_id}")
    text = got.json().get("goal_text") or got.json().get("goal") or ""
    assert "Deploy billing-api to prod" in text, f"template not rendered; got: {text!r}"
