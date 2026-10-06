"""TRG-35: Alertmanager / Datadog integrations go through the TriggerDispatcher.

They submitted goals directly (no dedup, no rate limit), so every
repeat_interval re-send of the same alert created another goal, and a failed
goal creation still answered 200, so the sender never retried.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router
from app.triggers.dispatcher import TriggerDispatcher
from tests.tenancy.test_plan_resolver import plan_db


class _Goals:
    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[dict[str, Any]] = []
        self._fail = fail

    async def create_goal(self, **kwargs: Any) -> Any:
        if self._fail:
            raise RuntimeError("db down")
        self.created.append(kwargs)
        return SimpleNamespace(goal_id=f"g-{len(self.created)}")


class _Redis:
    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    async def set(self, key: str, value: Any, *, ex: int | None = None, nx: bool = False) -> Any:
        if nx and key in self._d:
            return None
        self._d[key] = value
        return True

    async def incr(self, key: str) -> int:
        self._d[key] = int(self._d.get(key, 0)) + 1
        return int(self._d[key])

    async def expire(self, *_a: Any, **_k: Any) -> None:
        return None

    async def get(self, key: str) -> Any:
        return self._d.get(key)

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._d.pop(k, None)


def _client(goals: _Goals) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.goal_service = goals
    app.state.trigger_dispatcher = TriggerDispatcher(goal_service=goals, redis=_Redis())
    app.state.db_session_factory = plan_db({"t-ops": "enterprise"})
    return TestClient(app, raise_server_exceptions=False)


def _path(suffix: str) -> str:
    for r in router.routes:
        if getattr(r, "path", "").endswith(suffix):
            return str(r.path)
    raise AssertionError(suffix)


def _alert(starts_at: str = "2026-09-30T10:00:00Z") -> dict[str, Any]:
    return {
        "status": "firing",
        "fingerprint": "c3a1f0d2e4b5a697",
        "startsAt": starts_at,
        "labels": {"alertname": "HighCPU", "severity": "critical"},
        "annotations": {"summary": "CPU > 95%"},
    }


@pytest.fixture
def am_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERTMANAGER_WEBHOOK_TOKEN", "am-token")
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "t-ops")


def _post_am(client: TestClient, alerts: list[dict[str, Any]]) -> Any:
    return client.post(
        _path("/events/alertmanager"),
        json={"alerts": alerts},
        headers={"Authorization": "Bearer am-token"},
    )


def test_repeated_alertmanager_notification_creates_one_goal(am_env: None) -> None:
    goals = _Goals()
    client = _client(goals)

    first = _post_am(client, [_alert()])
    repeat = _post_am(client, [_alert()])  # repeat_interval re-send

    assert first.status_code == 200 and repeat.status_code == 200
    assert first.json()["goals_created"] == 1
    assert repeat.json()["goals_created"] == 0
    assert len(goals.created) == 1
    assert "HighCPU" in goals.created[0]["goal_text"]
    assert goals.created[0]["tenant_ctx"].plan.value == "enterprise"


def test_a_new_firing_episode_creates_a_new_goal(am_env: None) -> None:
    goals = _Goals()
    client = _client(goals)
    _post_am(client, [_alert("2026-09-30T10:00:00Z")])
    _post_am(client, [_alert("2026-09-30T18:00:00Z")])
    assert len(goals.created) == 2


def test_alertmanager_goal_failure_is_a_503(am_env: None) -> None:
    r = _post_am(_client(_Goals(fail=True)), [_alert()])
    assert r.status_code == 503, r.text


class _FlakyGoals(_Goals):
    """Fails goal creation for one alert name until healed."""

    def __init__(self, failing: str) -> None:
        super().__init__()
        self.failing: str | None = failing

    async def create_goal(self, **kwargs: Any) -> Any:
        if self.failing and self.failing in str(kwargs.get("goal_text", "")):
            raise RuntimeError("db down")
        return await super().create_goal(**kwargs)


def _named_alert(name: str) -> dict[str, Any]:
    alert = _alert()
    alert["fingerprint"] = hashlib.sha256(name.encode()).hexdigest()[:16]
    alert["labels"] = {"alertname": name, "severity": "warning"}
    return alert


def test_partly_failed_alertmanager_batch_is_a_503_and_the_retry_fills_the_gap(
    am_env: None,
) -> None:
    """a07-F154-01: one failed alert in a batch answers 503 (not 200).

    A 200 told Alertmanager the whole batch was delivered, so the failed
    alert waited for repeat_interval (hours). The retry re-sends the batch:
    the delivered episode is deduplicated, the failed one is created.
    """
    goals = _FlakyGoals(failing="DiskFull")
    client = _client(goals)
    batch = [_named_alert("HighCPU"), _named_alert("DiskFull")]

    first = _post_am(client, batch)
    assert first.status_code == 503, first.text
    assert len(goals.created) == 1  # HighCPU went through

    goals.failing = None  # the outage is over; Alertmanager retries the batch
    retry = _post_am(client, batch)
    assert retry.status_code == 200, retry.text
    assert retry.json()["goals_created"] == 1
    assert sorted(
        "HighCPU" if "HighCPU" in g["goal_text"] else "DiskFull" for g in goals.created
    ) == ["DiskFull", "HighCPU"]  # one goal per episode, no duplicate


def _post_dd(client: TestClient, body: dict[str, Any]) -> Any:
    raw = json.dumps(body)
    sig = hmac.new(b"dd-secret", raw.encode(), hashlib.sha256).hexdigest()
    return client.post(
        _path("/events/datadog"),
        content=raw,
        headers={"content-type": "application/json", "X-Datadog-Signature": sig},
    )


@pytest.fixture
def dd_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATADOG_WEBHOOK_SECRET", "dd-secret")
    monkeypatch.setenv("DATADOG_TENANT_ID", "t-ops")


def test_repeated_datadog_event_creates_one_goal(dd_env: None) -> None:
    goals = _Goals()
    client = _client(goals)
    body = {"id": "evt-42", "title": "DB down", "text": "primary", "alert_type": "error"}

    assert _post_dd(client, body).json()["goal_id"] == "g-1"
    again = _post_dd(client, body)

    assert again.status_code == 200
    assert again.json()["goal_id"] is None
    assert len(goals.created) == 1


def test_datadog_goal_failure_is_a_503(dd_env: None) -> None:
    body = {"id": "evt-43", "title": "DB down", "text": "primary", "alert_type": "critical"}
    assert _post_dd(_client(_Goals(fail=True)), body).status_code == 503
