"""GET /schedules/plan-floors: the UI reads the schedule minimums from the backend.

The frontend hard-coded the plan floors (planFloors.ts mirrored
PLAN_MIN_SCHEDULE_INTERVAL_SECONDS), so SCHEDULE_MIN_INTERVAL_*_S overrides
never reached the hints and pre-filled values in the trigger forms.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.api.test_schedules_api import _VALID_KEY, _make_app


def _get(client: TestClient) -> dict:
    r = client.get("/schedules/plan-floors", headers={"X-API-Key": _VALID_KEY})
    assert r.status_code == 200, r.text
    return r.json()


def test_plan_floors_default_to_the_shipped_values() -> None:
    body = _get(TestClient(_make_app()))
    assert body["plan_min_interval_seconds"] == {
        "free": 900,
        "starter": 300,
        "professional": 60,
        "enterprise": 60,
    }
    assert body["api_poll_default_interval_seconds"] == 300


def test_plan_floors_follow_settings_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "schedule_min_interval_free_s", 120)
    monkeypatch.setattr(get_settings(), "schedule_min_interval_enterprise_s", 600)
    floors = _get(TestClient(_make_app()))["plan_min_interval_seconds"]
    assert floors["free"] == 120
    assert floors["enterprise"] == 600


def test_plan_floors_require_auth_and_are_not_a_schedule_id() -> None:
    client = TestClient(_make_app())
    assert client.get("/schedules/plan-floors").status_code == 401
