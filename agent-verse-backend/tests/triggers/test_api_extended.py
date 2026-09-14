"""Tests for extended trigger API endpoints — PATCH, rotate-secret, validate-condition, typed webhooks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router
from app.triggers.store import ScheduleStore


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)
    store = ScheduleStore()
    application.state.schedule_store = store
    application.state.db = None
    application.state.trigger_dispatcher = None

    @application.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free", api_key="k")
        return await call_next(req)

    return application


@pytest.fixture
def client(app):
    return TestClient(app)


_TYPE_REQUIRED_DEFAULTS = {
    "cron": {"cron_expression": "0 9 * * *"},
    "interval": {"interval_seconds": 3600},
    "once": {"fire_at_iso": "2030-01-01T00:00:00Z"},
    "api_poll": {"poll_url": "https://example.com/status"},
    "rss_feed": {"rss_url": "https://example.com/feed.xml"},
    "db_row_change": {"db_table": "orders"},
}


def create_trigger(client, trigger_type="cron", **extra):
    # Seed the type's required fields so tests exercise real, valid specs (server
    # validation now rejects misconfigured ones).
    spec = {"trigger_type": trigger_type, **_TYPE_REQUIRED_DEFAULTS.get(trigger_type, {})}
    spec.update(extra)
    resp = client.post("/triggers", json={
        "spec": spec,
        "goal_id": "",
        "goal_template": "Test goal template",
    })
    assert resp.status_code == 201
    return resp.json()["schedule_id"]


# ── PATCH ─────────────────────────────────────────────────────────────────────

def test_patch_goal_template(client):
    sid = create_trigger(client)
    resp = client.patch(f"/triggers/{sid}", json={"goal_template": "Updated template"})
    assert resp.status_code == 200
    assert resp.json()["goal_template"] == "Updated template"


def test_patch_pause(client):
    sid = create_trigger(client)
    resp = client.patch(f"/triggers/{sid}", json={"paused": True})
    assert resp.status_code == 200
    assert resp.json()["paused"] is True


def test_patch_resume(client):
    sid = create_trigger(client)
    client.post(f"/triggers/{sid}/pause")
    resp = client.patch(f"/triggers/{sid}", json={"paused": False})
    assert resp.status_code == 200
    assert resp.json()["paused"] is False


def test_patch_nonexistent_returns_404(client):
    resp = client.patch("/triggers/nonexistent", json={"paused": True})
    assert resp.status_code == 404


def test_patch_spec_fields(client):
    sid = create_trigger(client)
    resp = client.patch(f"/triggers/{sid}", json={
        "spec": {"trigger_type": "interval", "interval_seconds": 7200}
    })
    assert resp.status_code == 200


# ── Rotate secret ─────────────────────────────────────────────────────────────

def test_rotate_secret_returns_new_secret(client):
    sid = create_trigger(client, trigger_type="webhook")
    resp = client.post(f"/triggers/{sid}/rotate-secret")
    assert resp.status_code == 200
    data = resp.json()
    assert "new_secret" in data
    assert len(data["new_secret"]) == 64  # 32 bytes hex
    assert data["status"] == "rotation_started"
    assert data["grace_period_seconds"] == 300


def test_rotate_secret_unknown_trigger(client):
    resp = client.post("/triggers/unknown-id/rotate-secret")
    assert resp.status_code == 404


def test_rotate_secret_unique_each_call(client):
    sid = create_trigger(client, trigger_type="webhook")
    r1 = client.post(f"/triggers/{sid}/rotate-secret").json()["new_secret"]
    r2 = client.post(f"/triggers/{sid}/rotate-secret").json()["new_secret"]
    assert r1 != r2


# ── Validate condition ────────────────────────────────────────────────────────

def test_validate_condition_empty_valid(client):
    resp = client.post("/triggers/validate-condition", json={"expression": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["evaluated_to"] is True


def test_validate_condition_with_payload(client):
    resp = client.post("/triggers/validate-condition", json={
        "expression": "",
        "test_payload": {"amount": 150, "currency": "USD"},
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


# ── Typed webhook ─────────────────────────────────────────────────────────────

def test_typed_webhook_accepted(client):
    resp = client.post(
        "/triggers/webhooks/github/test-token",
        json={"ref": "refs/heads/main", "repository": {"full_name": "org/repo"}},
        headers={"x-github-event": "push", "x-hub-signature-256": "sha256=invalid"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["webhook_type"] == "github"


def test_typed_webhook_stripe(client):
    resp = client.post(
        "/triggers/webhooks/stripe/test-token",
        json={"type": "payment_intent.succeeded", "id": "evt_001"},
        headers={"stripe-signature": "t=123,v1=invalid"},
    )
    assert resp.status_code == 200
    assert resp.json()["webhook_type"] == "stripe"


def test_typed_webhook_pagerduty(client):
    resp = client.post(
        "/triggers/webhooks/pagerduty/test-token",
        json={"event": {"event_type": "incident.trigger", "data": {"id": "Q1"}}},
    )
    assert resp.status_code == 200


def test_typed_webhook_generic_fallback(client):
    resp = client.post(
        "/triggers/webhooks/custom/test-token",
        json={"event": "custom_event"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


# ── Extended list ─────────────────────────────────────────────────────────────

def test_list_all_trigger_types(client):
    """Create one of each major (supported, valid) type and verify all appear."""
    specs = [
        {"trigger_type": "cron", "cron_expression": "0 9 * * *"},
        {"trigger_type": "goal_completed"},
        {"trigger_type": "webhook"},
        {"trigger_type": "interval", "interval_seconds": 3600},
        {"trigger_type": "once", "fire_at_iso": "2030-01-01T00:00:00Z"},
    ]
    for spec in specs:
        r = client.post("/triggers", json={
            "spec": spec,
            "goal_id": "",
            "goal_template": f"Template for {spec['trigger_type']}",
        })
        assert r.status_code == 201, r.text
    resp = client.get("/triggers")
    assert resp.status_code == 200
    types_in_list = {t["spec"]["trigger_type"] for t in resp.json()}
    assert {"cron", "goal_completed", "webhook", "interval", "once"} <= types_in_list
