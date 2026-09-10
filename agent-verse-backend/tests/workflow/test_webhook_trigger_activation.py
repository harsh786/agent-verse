"""Trigger activation (items 3 & 4): stateless webhook tokens, the public
``/wf-hooks/{token}`` endpoint, and the trigger-shape extraction shared by the
webhook endpoint and the schedule beat scan."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.workflow.trigger_extract import extract_triggers, schedule_cron
from app.workflow.webhook_router import router as webhook_router
from app.workflow.webhook_tokens import make_webhook_token, verify_webhook_token

# ── webhook_tokens ────────────────────────────────────────────────────────────


def test_token_round_trips():
    tok = make_webhook_token("tenant-1", "wf-1")
    assert verify_webhook_token(tok) == ("tenant-1", "wf-1")


def test_token_rejects_tampering():
    tok = make_webhook_token("tenant-1", "wf-1")
    assert verify_webhook_token(tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")) is None
    assert verify_webhook_token("not-a-token") is None
    assert verify_webhook_token("") is None


def test_token_ids_with_colons_survive():
    # payload splits tenant:workflow on the FIRST colon, so a workflow id that
    # itself contains a colon must still round-trip.
    tok = make_webhook_token("tenant-1", "wf:with:colons")
    assert verify_webhook_token(tok) == ("tenant-1", "wf:with:colons")


def test_token_invalidated_by_secret_change(monkeypatch):
    monkeypatch.setenv("WORKFLOW_WEBHOOK_SECRET", "secret-A")
    tok = make_webhook_token("t", "w")
    monkeypatch.setenv("WORKFLOW_WEBHOOK_SECRET", "secret-B")
    assert verify_webhook_token(tok) is None


# ── trigger_extract ───────────────────────────────────────────────────────────


def test_extract_triggers_plural_and_singular():
    assert extract_triggers({"triggers": [{"type": "webhook"}]}) == [{"type": "webhook"}]
    assert extract_triggers({"trigger": {"type": "api"}}) == [{"type": "api"}]
    both = extract_triggers({"triggers": [{"type": "webhook"}], "trigger": {"type": "schedule"}})
    assert {t["type"] for t in both} == {"webhook", "schedule"}


def test_extract_triggers_handles_junk():
    assert extract_triggers(None) == []
    assert extract_triggers({}) == []
    assert extract_triggers({"triggers": "nope"}) == []
    assert extract_triggers({"triggers": [None, 3, {"type": "webhook"}]}) == [{"type": "webhook"}]


def test_schedule_cron_nested_and_flat():
    assert schedule_cron({"type": "schedule", "schedule": {"cron": "* * * * *", "timezone": "UTC"}}) == (
        "* * * * *",
        "UTC",
    )
    assert schedule_cron({"type": "schedule", "cron": "0 9 * * 1", "timezone": "Asia/Kolkata"}) == (
        "0 9 * * 1",
        "Asia/Kolkata",
    )
    assert schedule_cron({"type": "schedule"}) == ("", "UTC")


# ── webhook endpoint ──────────────────────────────────────────────────────────


def _client(wf: dict | None, run_id: str = "run-xyz") -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(webhook_router)
    svc = AsyncMock()
    svc.get = AsyncMock(return_value=wf)
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=run_id)
    app.state.workflow_service = svc
    app.state.workflow_runner = runner
    return TestClient(app), runner


def test_endpoint_rejects_invalid_token():
    client, _ = _client(None)
    assert client.post("/wf-hooks/bogus.token", json={}).status_code == 401


def test_endpoint_unknown_workflow_404():
    client, _ = _client(None)
    tok = make_webhook_token("t", "missing")
    assert client.post(f"/wf-hooks/{tok}", json={}).status_code == 404


def test_endpoint_unpublished_409():
    client, _ = _client({"status": "draft", "definition": {"triggers": [{"type": "webhook"}]}})
    tok = make_webhook_token("t", "w")
    assert client.post(f"/wf-hooks/{tok}", json={}).status_code == 409


def test_endpoint_no_webhook_trigger_400():
    client, _ = _client(
        {"status": "published", "definition": {"trigger": {"type": "schedule"}}}
    )
    tok = make_webhook_token("t", "w")
    assert client.post(f"/wf-hooks/{tok}", json={}).status_code == 400


def test_endpoint_fires_run_with_webhook_trigger_type():
    wf = {"status": "published", "definition": {"triggers": [{"type": "webhook"}]}}
    client, runner = _client(wf, run_id="run-777")
    tok = make_webhook_token("tenant-9", "wf-9")
    resp = client.post(f"/wf-hooks/{tok}", json={"invoice": "INV-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "accepted", "run_id": "run-777", "workflow_id": "wf-9"}
    kwargs = runner.run.await_args.kwargs
    assert kwargs["trigger_type"] == "webhook"
    assert kwargs["tenant_id"] == "tenant-9"
    assert kwargs["inputs"] == {"invoice": "INV-1"}


def test_endpoint_accepts_api_trigger_type_singular_shape():
    wf = {"status": "published", "definition": {"trigger": {"type": "api"}}}
    client, runner = _client(wf)
    tok = make_webhook_token("t", "w")
    assert client.post(f"/wf-hooks/{tok}", json={}).status_code == 200


# ── schedule scan helper (cron bounds) ────────────────────────────────────────


def test_cron_bounds_prev_before_now_next_after():
    from app.workflow.celery_tasks import _cron_bounds

    now = datetime(2026, 9, 10, 19, 33, 30, tzinfo=UTC)
    bounds = _cron_bounds("* * * * *", now, "UTC")
    assert bounds is not None
    prev, nxt = bounds
    assert prev <= now < nxt
    assert (now - prev).total_seconds() <= 60
    assert (nxt - now).total_seconds() <= 60


def test_cron_bounds_invalid_returns_none():
    from app.workflow.celery_tasks import _cron_bounds

    assert _cron_bounds("not a cron", datetime.now(UTC), "UTC") is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
