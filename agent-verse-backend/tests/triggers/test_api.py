"""Tests for the triggers API router."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router
from app.triggers.store import ScheduleStore

# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)
    store = ScheduleStore()
    application.state.schedule_store = store
    application.state.db = None
    application.state.trigger_dispatcher = None

    # Wire a fake tenant context via middleware
    @application.middleware("http")
    async def inject_tenant(request, call_next):
        request.state.tenant = SimpleNamespace(
            tenant_id="t1",
            plan="free",
            api_key="k",
        )
        return await call_next(request)

    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# ── List (empty) ──────────────────────────────────────────────────────────────

def test_list_triggers_empty(client):
    resp = client.get("/triggers")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_cron_trigger(client):
    resp = client.post("/triggers", json={
        "spec": {
            "trigger_type": "cron",
            "cron_expression": "0 9 * * 1-5",
            "description": "Daily standup trigger",
        },
        "goal_id": "g-001",
        "goal_template": "Run daily standup report",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["spec"]["trigger_type"] == "cron"
    assert data["goal_template"] == "Run daily standup report"
    assert data["paused"] is False


def test_create_trigger_bound_to_goal_id_only(client):
    """A trigger may bind a concrete goal_id with no template/agent — re-runs THAT
    goal on fire (avoids the noise of a free-text template matching many goals)."""
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "cron", "cron_expression": "0 9 * * *"},
        "goal_id": "g-bound-123",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["goal_id"] == "g-bound-123"


def test_create_trigger_requires_goal_id_template_or_agent(client):
    """With none of goal_id / goal_template / agent_id, creation is rejected (422)."""
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "cron", "cron_expression": "0 9 * * *"},
    })
    assert resp.status_code == 422


def test_list_triggers_surface_created_at_timestamp(client):
    """Every listed trigger carries a created_at timestamp so the UI can show it."""
    client.post("/triggers", json={
        "spec": {"trigger_type": "cron", "cron_expression": "0 9 * * *"},
        "goal_id": "g-ts-1",
    })
    listed = client.get("/triggers").json()
    assert listed, "expected at least one trigger"
    assert listed[0].get("created_at"), "created_at must be serialized for the UI"
    assert listed[0]["goal_id"] == "g-ts-1"


def test_advanced_spec_fields_round_trip(client):
    """Previously-drifted / advanced spec fields must survive create → list.

    The frontend used to emit drifted names (run_at, condition_cel, webhook_secret,
    warn_before_seconds, max_firings) that the backend silently dropped. With the
    contract aligned, the real backend field names must round-trip intact — proving
    advanced config actually persists (the root cause of "triggers won't work in
    production").
    """
    resp = client.post("/triggers", json={
        "spec": {
            "trigger_type": "cron",
            "cron_expression": "0 9 * * *",
            "timezone": "America/New_York",
            "description": "Morning digest",
            # cross-cutting advanced params
            "condition": "payload.env == 'prod'",
            "priority": "high",
            "max_firings_per_hour": 5,
            "expires_at_iso": "2030-01-01T00:00:00Z",
            "tags": ["ops", "digest"],
            "simulation_mode": True,
        },
        "goal_id": "g-adv-1",
    })
    assert resp.status_code == 201, resp.text
    spec = client.get("/triggers").json()[0]["spec"]
    assert spec["cron_expression"] == "0 9 * * *"
    assert spec["timezone"] == "America/New_York"
    assert spec["condition"] == "payload.env == 'prod'"
    assert spec["priority"] == "high"
    assert spec["max_firings_per_hour"] == 5
    assert spec["expires_at_iso"] == "2030-01-01T00:00:00Z"
    assert spec["tags"] == ["ops", "digest"]
    assert spec["simulation_mode"] is True


def test_webhook_signature_secret_round_trips(client):
    """The aligned webhook HMAC field name persists (was 'webhook_secret' drift)."""
    resp = client.post("/triggers", json={
        "spec": {
            "trigger_type": "github_webhook",
            "webhook_signature_secret": "whsec_test_123",
            "github_event_filter": "push",
        },
        "goal_template": "handle webhook",
    })
    assert resp.status_code == 201, resp.text
    spec = client.get("/triggers").json()[0]["spec"]
    assert spec["webhook_signature_secret"] == "whsec_test_123"
    assert spec["github_event_filter"] == "push"


@pytest.mark.parametrize("trigger_type,extra", [
    ("once", {"fire_at_iso": "2030-01-01T09:00:00Z"}),
    ("interval", {"interval_seconds": 3600}),
    ("relative_delay", {"relative_offset_seconds": 300}),
    ("business_calendar", {"business_calendar_id": "cal-1"}),
    ("condition", {"condition_expression": "payload.x > 1"}),
    ("counter_threshold", {"counter_key": "k", "counter_threshold": 5}),
    ("compound", {"compound_trigger_ids": ["t1", "t2"]}),
    ("window_aggregate", {"window_field": "amount"}),
    ("webhook", {}),
    ("rest", {}),
    ("event", {}),
    ("api_poll", {"poll_url": "https://api.example.com/status"}),
    ("file_drop", {"file_drop_path": "/watch/inbox"}),
    ("cloudwatch", {}),
    ("state_transition", {"state_machine_id": "sm-1"}),
])
def test_reconciled_supported_types_are_creatable(client, trigger_type, extra):
    """Every trigger type the UI now offers (aligned to the backend dispatch map),
    given its required fields, must actually create — no more 'Unknown
    trigger_type' 422s from UI-only names like custom_webhook / kafka_message."""
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": trigger_type, **extra},
        "goal_id": f"g-{trigger_type}",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["spec"]["trigger_type"] == trigger_type


@pytest.mark.parametrize("spec,expect_msg", [
    ({"trigger_type": "cron"}, "cron_expression"),
    ({"trigger_type": "cron", "cron_expression": "not a cron"}, "Invalid cron"),
    ({"trigger_type": "interval", "interval_seconds": 0}, "interval_seconds > 0"),
    ({"trigger_type": "once"}, "fire_at_iso"),
    ({"trigger_type": "once", "fire_at_iso": "nonsense"}, "valid ISO"),
    ({"trigger_type": "api_poll"}, "poll_url"),
    ({"trigger_type": "rss_feed"}, "rss_url"),
    ({"trigger_type": "db_row_change"}, "db_table"),
    ({"trigger_type": "condition"}, "condition_expression"),
    ({"trigger_type": "counter_threshold", "counter_key": "k"}, "counter_threshold > 0"),
    ({"trigger_type": "state_transition"}, "state_machine_id"),
    ({"trigger_type": "cron", "cron_expression": "0 9 * * *", "priority": "urgent"}, "priority"),
    ({"trigger_type": "cron", "cron_expression": "0 9 * * *", "max_firings_per_hour": -1}, "max_firings_per_hour"),
    ({"trigger_type": "cron", "cron_expression": "0 9 * * *", "expires_at_iso": "bad"}, "expires_at_iso"),
])
def test_misconfigured_spec_is_rejected(client, spec, expect_msg):
    """A recognised type with missing/invalid required fields is rejected (422)
    with a clear message — a broken trigger can never be persisted."""
    resp = client.post("/triggers", json={"spec": spec, "goal_id": "g-bad"})
    assert resp.status_code == 422, resp.text
    assert expect_msg in resp.json()["detail"], resp.json()


def test_create_goal_chain_trigger(client):
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "goal_completed"},
        "goal_id": "",
        "goal_template": "Follow-up after goal completed",
    })
    assert resp.status_code == 201
    assert resp.json()["spec"]["trigger_type"] == "goal_completed"


def test_create_unknown_type_returns_422(client):
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "completely_unknown"},
        "goal_id": "",
        "goal_template": "test",
    })
    assert resp.status_code == 422


def test_create_empty_template_returns_422(client):
    # Neither a goal template nor a referenced agent → nothing to run → 422.
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "cron"},
        "goal_id": "",
        "goal_template": "",  # empty
    })
    assert resp.status_code == 422


def test_create_agent_only_no_template_allowed(client):
    """A trigger may reference an agent and run its own goal — no goal template
    required in that case."""
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "webhook"},
        "goal_id": "",
        "agent_id": "agent-123",
        "goal_template": "",  # empty, but an agent is referenced
    })
    assert resp.status_code in (200, 201)


# ── Get ───────────────────────────────────────────────────────────────────────

def test_get_trigger(client):
    cr = client.post("/triggers", json={
        "spec": {"trigger_type": "interval", "interval_seconds": 3600},
        "goal_id": "",
        "goal_template": "Run hourly job",
    })
    schedule_id = cr.json()["schedule_id"]
    resp = client.get(f"/triggers/{schedule_id}")
    assert resp.status_code == 200
    assert resp.json()["schedule_id"] == schedule_id


def test_get_unknown_trigger_returns_404(client):
    resp = client.get("/triggers/nonexistent-id")
    assert resp.status_code == 404


# ── Pause / Resume ────────────────────────────────────────────────────────────

def test_pause_and_resume_trigger(client):
    cr = client.post("/triggers", json={
        "spec": {"trigger_type": "cron", "cron_expression": "0 * * * *"},
        "goal_id": "",
        "goal_template": "Hourly task",
    })
    sid = cr.json()["schedule_id"]

    pause_resp = client.post(f"/triggers/{sid}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["paused"] is True

    resume_resp = client.post(f"/triggers/{sid}/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["paused"] is False


def test_pause_unknown_returns_404(client):
    assert client.post("/triggers/no-such-id/pause").status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_trigger(client):
    cr = client.post("/triggers", json={
        "spec": {"trigger_type": "once", "fire_at_iso": "2030-01-01T00:00:00Z"},
        "goal_id": "",
        "goal_template": "One-shot task",
    })
    sid = cr.json()["schedule_id"]
    assert client.delete(f"/triggers/{sid}").status_code == 204
    assert client.get(f"/triggers/{sid}").status_code == 404


def test_delete_unknown_returns_404(client):
    assert client.delete("/triggers/nonexistent").status_code == 404


# ── List returns created trigger ──────────────────────────────────────────────

def test_list_returns_created_triggers(client):
    client.post("/triggers", json={
        "spec": {"trigger_type": "cron", "cron_expression": "0 * * * *"},
        "goal_id": "",
        "goal_template": "Trigger A",
    })
    client.post("/triggers", json={
        "spec": {"trigger_type": "goal_completed"},
        "goal_id": "",
        "goal_template": "Trigger B",
    })
    resp = client.get("/triggers")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Simulate ──────────────────────────────────────────────────────────────────

def test_simulate_trigger(client):
    cr = client.post("/triggers", json={
        "spec": {"trigger_type": "webhook"},
        "goal_id": "",
        "goal_template": "Handle GitHub event",
    })
    sid = cr.json()["schedule_id"]
    resp = client.post(f"/triggers/{sid}/simulate", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "trigger_type" in data or "simulated" in data


# ── Events (no DB) ─────────────────────────────────────────────────────────────

def test_list_events_no_db(client):
    cr = client.post("/triggers", json={
        "spec": {"trigger_type": "cron", "cron_expression": "0 * * * *"},
        "goal_id": "",
        "goal_template": "test",
    })
    sid = cr.json()["schedule_id"]
    resp = client.get(f"/triggers/{sid}/events")
    assert resp.status_code == 200
    assert resp.json() == []


# ── DLQ ──────────────────────────────────────────────────────────────────────

def test_list_dlq_no_db(client):
    resp = client.get("/triggers/dlq")
    assert resp.status_code == 200
    assert resp.json() == []


def test_retry_dlq_entry(client):
    resp = client.post("/triggers/dlq/fake-dlq-id/retry")
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


# ── 2.W-10: unsupported trigger types are rejected at registration ────────────

def test_create_rejects_unsupported_trigger_type(client):
    """A type with no runtime dispatch path (google_sheets) must be refused so a
    tenant cannot register a trigger that could never fire."""
    resp = client.post("/triggers", json={
        "spec": {
            "trigger_type": "google_sheets",
            "description": "poll a sheet that never fires",
        },
        "goal_template": "handle {{payload}}",
    })
    assert resp.status_code == 422
    assert "not yet supported" in resp.json()["detail"]


def test_create_accepts_supported_consumer_type(client):
    """A supported type (goal_completed → chain consumer) is accepted."""
    resp = client.post("/triggers", json={
        "spec": {
            "trigger_type": "goal_completed",
            "description": "chain off a completed goal",
        },
        "goal_template": "follow up on {{payload.goal_id}}",
    })
    assert resp.status_code == 201


# ── 2.W-1: EVENT emission endpoint ────────────────────────────────────────────

def test_emit_event_publishes_with_tenant_stamped(app, client):
    import json as _json

    published = []

    class _FakeRedis:
        def publish(self, channel, data):
            published.append((channel, data))

    app.state.trigger_event_redis = _FakeRedis()
    resp = client.post("/triggers/events/deployments", json={"sha": "abc"})
    assert resp.status_code == 202
    assert resp.json()["event_channel"] == "deployments"
    assert len(published) == 1
    channel, data = published[0]
    assert channel == "trigger:event:deployments"
    body = _json.loads(data)
    assert body["tenant_id"] == "t1"  # stamped server-side from the auth context
    assert body["sha"] == "abc"


def test_emit_event_503_without_bus(app, client):
    app.state.trigger_event_redis = None
    resp = client.post("/triggers/events/deployments", json={})
    assert resp.status_code == 503
