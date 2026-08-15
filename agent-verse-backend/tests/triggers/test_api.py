"""Tests for the triggers API router."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from types import SimpleNamespace

from app.api.triggers import router
from app.triggers.store import ScheduleStore
from app.triggers.models import TriggerSpec, TriggerType


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
    resp = client.post("/triggers", json={
        "spec": {"trigger_type": "cron"},
        "goal_id": "",
        "goal_template": "",  # empty
    })
    assert resp.status_code == 422


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
        "spec": {"trigger_type": "once"},
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
