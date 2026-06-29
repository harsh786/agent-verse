"""Extra coverage tests for app/api/schedules.py — webhook, pause/resume, fire, SSE."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.schedules import (
    events_router,
    nl_router,
    router as schedules_router,
    webhooks_router,
    _record_to_dict,
    _spec_to_dict,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

_CTX = TenantContext(tenant_id="tid-sched-extra", plan=PlanTier.PROFESSIONAL, api_key_id="kid-extra")
_VALID_KEY = "av_test_sched_extra"


def _make_app(
    schedule_store: ScheduleStore | None = None,
    nl_scheduler: Any | None = None,
    agent_store: Any | None = None,
    goal_service: Any | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(schedules_router)
    app.include_router(nl_router)
    app.include_router(webhooks_router)
    app.include_router(events_router)
    app.state.schedule_store = schedule_store or ScheduleStore()
    app.state.nl_scheduler = nl_scheduler or AsyncMock()
    if agent_store is not None:
        app.state.agent_store = agent_store
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


_HEADERS = {"X-API-Key": _VALID_KEY}


# ---------------------------------------------------------------------------
# _spec_to_dict helper
# ---------------------------------------------------------------------------

def test_spec_to_dict():
    spec = TriggerSpec(
        trigger_type=TriggerType.CRON,
        cron_expression="0 9 * * 1-5",
        timezone="America/New_York",
        interval_seconds=0,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        description="Daily standup",
    )
    d = _spec_to_dict(spec)
    assert d["trigger_type"] == TriggerType.CRON
    assert d["cron_expression"] == "0 9 * * 1-5"
    assert d["description"] == "Daily standup"


# ---------------------------------------------------------------------------
# _record_to_dict helper
# ---------------------------------------------------------------------------

def test_record_to_dict_with_trigger_spec():
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK)
    rec = {
        "schedule_id": "s1",
        "spec": spec,
        "goal_id": "g1",
    }
    out = _record_to_dict(rec)
    assert out["agent_id"] == ""
    assert isinstance(out["spec"], dict)


def test_record_to_dict_without_spec():
    rec = {"schedule_id": "s2", "goal_id": "g2"}
    out = _record_to_dict(rec)
    assert out["agent_id"] == ""
    assert out["goal_template"] == "g2"


# ---------------------------------------------------------------------------
# Webhook schedule creation — token is generated
# ---------------------------------------------------------------------------

def test_create_schedule_webhook_generates_token():
    client = TestClient(_make_app())
    resp = client.post(
        "/schedules",
        json={
            "trigger_type": "webhook",
            "name": "My Webhook",
            "goal_template": "Process webhook data",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    data = resp.json()
    # The token is stored internally; schedule should have spec
    assert "spec" in data


@pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning")
@pytest.mark.filterwarnings("ignore:.*HTTP_422_UNPROCESSABLE_ENTITY.*:DeprecationWarning")
def test_create_schedule_invalid_trigger_type():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = TestClient(_make_app())
        resp = client.post(
            "/schedules",
            json={"trigger_type": "invalid_type", "goal_template": "test"},
            headers=_HEADERS,
        )
    assert resp.status_code in (400, 422)


def test_create_schedule_requires_auth():
    client = TestClient(_make_app())
    resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "goal_template": "test"},
    )
    assert resp.status_code == 401


def test_create_schedule_agent_not_found():
    class _FakeAgentStore:
        def get(self, agent_id: str, *, tenant_ctx: Any) -> None:
            return None

    client = TestClient(_make_app(agent_store=_FakeAgentStore()))
    resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "agent_id": "ghost-agent", "goal_template": "test"},
        headers=_HEADERS,
    )
    assert resp.status_code == 404


def test_create_schedule_agent_store_unavailable():
    """When agent_store is not set but agent_id is provided, raises 500."""
    app = _make_app()
    # Remove agent_store attribute if present
    if hasattr(app.state, "agent_store"):
        del app.state.agent_store
    client = TestClient(app)
    resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "agent_id": "some-agent", "goal_template": "test"},
        headers=_HEADERS,
    )
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# pause / resume endpoints
# ---------------------------------------------------------------------------

def test_pause_schedule_success():
    store = ScheduleStore()
    client = TestClient(_make_app(schedule_store=store))

    # First create a schedule
    resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "goal_template": "Pause test"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    schedule_id = resp.json()["schedule_id"]

    # Pause it
    resp = client.post(f"/schedules/{schedule_id}/pause", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["paused"] is True


def test_pause_schedule_not_found():
    client = TestClient(_make_app())
    resp = client.post("/schedules/ghost-id/pause", headers=_HEADERS)
    assert resp.status_code == 404


def test_resume_schedule_success():
    store = ScheduleStore()
    client = TestClient(_make_app(schedule_store=store))

    resp = client.post(
        "/schedules",
        json={"trigger_type": "cron", "cron_expr": "0 * * * *", "goal_template": "Resume test"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    schedule_id = resp.json()["schedule_id"]

    # Pause then resume
    client.post(f"/schedules/{schedule_id}/pause", headers=_HEADERS)
    resp = client.post(f"/schedules/{schedule_id}/resume", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["paused"] is False


def test_resume_schedule_not_found():
    client = TestClient(_make_app())
    resp = client.post("/schedules/ghost-id/resume", headers=_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# fire endpoint
# ---------------------------------------------------------------------------

def test_fire_schedule_rest_type():
    store = ScheduleStore()
    mock_goal_svc = AsyncMock()
    mock_goal_svc.submit_goal.return_value = {"goal_id": "fired-goal-1"}

    client = TestClient(_make_app(schedule_store=store, goal_service=mock_goal_svc))

    # Create a REST schedule
    resp = client.post(
        "/schedules",
        json={"trigger_type": "rest", "goal_template": "Manual task"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    schedule_id = resp.json()["schedule_id"]

    resp = client.post(f"/schedules/{schedule_id}/fire", headers=_HEADERS)
    assert resp.status_code == 202
    assert resp.json()["fired"] is True
    assert resp.json()["goal_id"] == "fired-goal-1"


def test_fire_schedule_not_found():
    client = TestClient(_make_app())
    resp = client.post("/schedules/ghost-id/fire", headers=_HEADERS)
    assert resp.status_code == 404


def test_fire_schedule_wrong_type():
    """Only REST and webhook can be manually fired."""
    store = ScheduleStore()
    client = TestClient(_make_app(schedule_store=store))

    # Create a CRON schedule
    resp = client.post(
        "/schedules",
        json={"trigger_type": "cron", "cron_expr": "0 * * * *", "goal_template": "cron task"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    schedule_id = resp.json()["schedule_id"]

    resp = client.post(f"/schedules/{schedule_id}/fire", headers=_HEADERS)
    assert resp.status_code == 400


def test_fire_schedule_webhook_type():
    store = ScheduleStore()
    mock_goal_svc = AsyncMock()
    mock_goal_svc.submit_goal.return_value = {"goal_id": "fired-webhook-1"}

    client = TestClient(_make_app(schedule_store=store, goal_service=mock_goal_svc))

    resp = client.post(
        "/schedules",
        json={"trigger_type": "webhook", "goal_template": "Webhook task"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    schedule_id = resp.json()["schedule_id"]

    resp = client.post(f"/schedules/{schedule_id}/fire", headers=_HEADERS)
    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Webhook trigger endpoint
# ---------------------------------------------------------------------------

def test_webhook_trigger_known_token():
    app = _make_app()
    client = TestClient(app)

    # Register a webhook schedule (with token)
    resp = client.post(
        "/schedules",
        json={"trigger_type": "webhook", "name": "My Webhook"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201

    # Extract the token from the token_map
    token_map = app.state._webhook_tokens
    assert len(token_map) > 0
    token = list(token_map.keys())[0]

    # Hit the webhook endpoint
    resp = client.post(f"/webhooks/{token}", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_webhook_trigger_unknown_token():
    client = TestClient(_make_app())
    resp = client.post("/webhooks/unknown-ghost-token", headers=_HEADERS)
    assert resp.status_code == 404


def test_webhook_trigger_requires_auth():
    client = TestClient(_make_app())
    resp = client.post("/webhooks/any-token")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# NL schedule creation
# ---------------------------------------------------------------------------

def test_nl_create_schedule_cron():
    mock_nl = AsyncMock()
    mock_nl.parse = AsyncMock(return_value=[
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * 1-5", description="Daily 9am")
    ])
    client = TestClient(_make_app(nl_scheduler=mock_nl))
    resp = client.post(
        "/nl/schedule",
        json={"command": "every weekday at 9am"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    result = resp.json()
    assert isinstance(result, list)
    assert len(result) == 1


def test_nl_create_schedule_webhook_type():
    """NL parser returning WEBHOOK type adds token."""
    mock_nl = AsyncMock()
    mock_nl.parse = AsyncMock(return_value=[
        TriggerSpec(trigger_type=TriggerType.WEBHOOK, description="Webhook schedule")
    ])
    app = _make_app(nl_scheduler=mock_nl)
    client = TestClient(app)
    resp = client.post(
        "/nl/schedule",
        json={"command": "on webhook event"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    # Token map should have an entry
    assert len(app.state._webhook_tokens) > 0


def test_nl_create_schedule_multiple_specs():
    mock_nl = AsyncMock()
    mock_nl.parse = AsyncMock(return_value=[
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *"),
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 18 * * *"),
    ])
    client = TestClient(_make_app(nl_scheduler=mock_nl))
    resp = client.post(
        "/nl/schedule",
        json={"command": "every day at 9am and 6pm"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    assert len(resp.json()) == 2


def test_nl_create_schedule_empty():
    mock_nl = AsyncMock()
    mock_nl.parse = AsyncMock(return_value=[])
    client = TestClient(_make_app(nl_scheduler=mock_nl))
    resp = client.post(
        "/nl/schedule",
        json={"command": "unclear command"},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json() == []


# ---------------------------------------------------------------------------
# List schedules
# ---------------------------------------------------------------------------

def test_list_schedules_empty():
    client = TestClient(_make_app())
    resp = client.get("/schedules", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_schedules_with_records():
    store = ScheduleStore()
    client = TestClient(_make_app(schedule_store=store))

    client.post(
        "/schedules",
        json={"trigger_type": "once", "goal_template": "task-1"},
        headers=_HEADERS,
    )
    resp = client.get("/schedules", headers=_HEADERS)
    assert resp.status_code == 200
    schedules = resp.json()
    assert len(schedules) == 1


# ---------------------------------------------------------------------------
# Get specific schedule
# ---------------------------------------------------------------------------

def test_get_schedule_success():
    store = ScheduleStore()
    client = TestClient(_make_app(schedule_store=store))

    resp = client.post(
        "/schedules",
        json={"trigger_type": "interval", "interval_seconds": 60, "goal_template": "poll"},
        headers=_HEADERS,
    )
    schedule_id = resp.json()["schedule_id"]

    resp = client.get(f"/schedules/{schedule_id}", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["schedule_id"] == schedule_id


def test_get_schedule_not_found():
    client = TestClient(_make_app())
    resp = client.get("/schedules/ghost-id", headers=_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete schedule
# ---------------------------------------------------------------------------

def test_delete_schedule_success():
    store = ScheduleStore()
    client = TestClient(_make_app(schedule_store=store))

    resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "goal_template": "delete me"},
        headers=_HEADERS,
    )
    schedule_id = resp.json()["schedule_id"]

    resp = client.delete(f"/schedules/{schedule_id}", headers=_HEADERS)
    assert resp.status_code == 204


def test_delete_schedule_not_found():
    client = TestClient(_make_app())
    resp = client.delete("/schedules/ghost-id", headers=_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SSE events stream
# ---------------------------------------------------------------------------

def test_events_stream_requires_auth():
    client = TestClient(_make_app())
    resp = client.get("/events")
    assert resp.status_code == 401
