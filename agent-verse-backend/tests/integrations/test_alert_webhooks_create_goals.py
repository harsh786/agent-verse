"""Regression: Alertmanager / Datadog webhooks must actually create goals.

Both routes called ``goal_service.submit_goal(goal=..., tenant_ctx=...)`` without
the required ``priority`` / ``dry_run`` arguments. The resulting TypeError was
swallowed by a broad ``except`` so the webhook answered 200 with
``goals_created: 0`` and no goal was ever created. The GoalService here is
autospecced from the real class, so a call that does not match the real
signature fails the test instead of passing silently like a bare AsyncMock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import create_autospec

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router
from app.services.goal_service import GoalService


def _app() -> tuple[TestClient, object]:
    app = FastAPI()
    app.include_router(router)
    goal_service = create_autospec(GoalService, instance=True)
    # TRG-35: alerts go through the TriggerDispatcher, which calls create_goal.
    goal_service.create_goal.return_value = {"goal_id": "g-alert"}
    app.state.goal_service = goal_service
    return TestClient(app), goal_service


def _route_prefix() -> str:
    for r in router.routes:
        path = getattr(r, "path", "")
        if path.endswith("/events/alertmanager"):
            return path.removesuffix("/events/alertmanager")
    raise AssertionError("alertmanager route not found")


def test_alertmanager_firing_alert_creates_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERTMANAGER_WEBHOOK_TOKEN", "am-token")
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "tenant-ops")
    client, goal_service = _app()

    r = client.post(
        f"{_route_prefix()}/events/alertmanager",
        json={
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighCPU", "severity": "critical"},
                    "annotations": {"summary": "CPU > 95%"},
                },
                {"status": "resolved", "labels": {"alertname": "Old"}},
            ]
        },
        headers={"Authorization": "Bearer am-token"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["goals_created"] == 1
    assert r.json()["goal_ids"] == ["g-alert"]
    goal_service.create_goal.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = goal_service.create_goal.await_args.kwargs  # type: ignore[attr-defined]
    assert "HighCPU" in kwargs["goal_text"]
    assert kwargs["idempotency_key"]
    assert kwargs["tenant_ctx"].tenant_id == "tenant-ops"


def test_datadog_critical_event_creates_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATADOG_WEBHOOK_SECRET", "dd-secret")
    monkeypatch.setenv("DATADOG_TENANT_ID", "tenant-dd")
    client, goal_service = _app()
    body = json.dumps({"title": "DB down", "text": "primary unreachable", "alert_type": "error"})
    sig = hmac.new(b"dd-secret", body.encode(), hashlib.sha256).hexdigest()

    r = client.post(
        f"{_route_prefix()}/events/datadog",
        content=body,
        headers={"content-type": "application/json", "X-Datadog-Signature": sig},
    )

    assert r.status_code == 200, r.text
    assert r.json()["goal_id"] == "g-alert"
    kwargs = goal_service.create_goal.await_args.kwargs  # type: ignore[attr-defined]
    assert "DB down" in kwargs["goal_text"]
    assert kwargs["tenant_ctx"].tenant_id == "tenant-dd"
