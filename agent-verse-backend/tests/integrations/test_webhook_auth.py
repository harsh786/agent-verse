"""Inbound integration webhooks fail closed (they sit under an auth-exempt prefix).

Regressions: Alertmanager had no authentication (anyone could submit autonomous
agent goals into the configured tenant); Datadog verified only when a secret was
set; the Zapier poll returned completed goals to anyone; the re-ingest webhooks
took tenant AND collection from headers and queued ingestion of an
attacker-chosen source into any tenant's knowledge base.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router
from app.integrations.webhook_auth import reingest_signing_secret


class _Store:
    def __init__(self, owned: set[tuple[str, str]]) -> None:
        self.owned = owned

    async def get_collection_async(self, cid: str, *, tenant_ctx: Any) -> Any:
        return SimpleNamespace(id=cid) if (tenant_ctx.tenant_id, cid) in self.owned else None


def _client(**state: Any) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    for k, v in state.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ALERTMANAGER_WEBHOOK_TOKEN", "DATADOG_WEBHOOK_SECRET", "REINGEST_WEBHOOK_SECRET",
                "ZAPIER_SECRET", "ZAPIER_WEBHOOK_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALERTMANAGER_TENANT_ID", "am")
    monkeypatch.setenv("ENVIRONMENT", "production")


def test_alertmanager_requires_the_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    gs = MagicMock(create_goal=AsyncMock(return_value={"goal_id": "g"}))
    client = _client(goal_service=gs)
    alert = {"alerts": [{"status": "firing", "labels": {"alertname": "X"}}]}
    assert client.post("/integrations/events/alertmanager", json=alert).status_code == 503
    monkeypatch.setenv("ALERTMANAGER_WEBHOOK_TOKEN", "tok")
    assert client.post("/integrations/events/alertmanager", json=alert).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/integrations/events/alertmanager", json=alert, headers=bad).status_code == 401
    gs.create_goal.assert_not_called()
    ok = client.post("/integrations/events/alertmanager", json=alert,
                     headers={"Authorization": "Bearer tok"})
    assert ok.status_code == 200 and ok.json()["goals_created"] == 1


def test_datadog_signature_is_always_required() -> None:
    client = _client()
    r = client.post("/integrations/events/datadog", json={"title": "t", "alert_type": "error"})
    assert r.status_code == 503  # unconfigured -> closed, not open


def test_zapier_poll_requires_the_secret() -> None:
    assert _client(goal_service=MagicMock()).get("/integrations/zapier/goals").status_code == 403


def test_reingest_webhook_requires_a_valid_per_collection_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store({("t1", "c1")})
    client = _client(knowledge_store=store)
    url = "/integrations/webhooks/github/push?tenant_id=t1&collection_id=c1"
    body = json.dumps({"repository": {"full_name": "evil/repo"}}).encode()
    assert client.post(url, content=body).status_code == 503  # not configured
    monkeypatch.setenv("REINGEST_WEBHOOK_SECRET", "master")
    assert client.post(url, content=body).status_code == 401  # unsigned

    # A secret for ANOTHER collection does not authenticate this one.
    other = reingest_signing_secret("t2", "c9") or ""
    forged = "sha256=" + hmac.new(other.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(url, content=body, headers={"X-Hub-Signature-256": forged}).status_code == 401

    secret = reingest_signing_secret("t1", "c1") or ""
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    with patch("app.scaling.tasks.delta_reingest_files") as task:
        task.delay.return_value = SimpleNamespace(id="task-1")
        r = client.post(url, content=body, headers={"X-Hub-Signature-256": good})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert task.delay.call_args.kwargs["tenant_id"] == "t1"

    # A correctly signed request for a collection the tenant does not own: 404.
    url2 = "/integrations/webhooks/github/push?tenant_id=t1&collection_id=c2"
    s2 = reingest_signing_secret("t1", "c2") or ""
    sig2 = "sha256=" + hmac.new(s2.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(url2, content=body, headers={"X-Hub-Signature-256": sig2}).status_code == 404


def test_tenant_can_fetch_only_its_own_collection_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import Request

    from app.api.knowledge import router as knowledge_router

    monkeypatch.setenv("REINGEST_WEBHOOK_SECRET", "master")
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id="t1", plan="free", api_key_id="k")
        return await call_next(request)

    app.include_router(knowledge_router)
    app.state.knowledge_store = _Store({("t1", "c1")})
    client = TestClient(app, raise_server_exceptions=False)
    ok = client.get("/knowledge/collections/c1/reingest-webhook")
    assert ok.status_code == 200 and ok.json()["secret"] == reingest_signing_secret("t1", "c1")
    assert client.get("/knowledge/collections/c2/reingest-webhook").status_code == 404


def test_slack_events_route_fails_closed_in_production_without_a_secret(
    monkeypatch,
) -> None:
    """Regression: /slack/events skipped verification entirely when no secret
    was configured, even in production."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.integrations import router

    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(
        "/integrations/slack/events", content=b'{"type":"event_callback"}'
    )
    assert r.status_code == 503


@pytest.mark.parametrize(
    "path", ["/integrations/slack/events", "/integrations/slack/commands",
             "/integrations/slack/interactive"],
)
def test_slack_routes_fail_closed_in_development_without_a_secret(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Regression: with no SLACK_SIGNING_SECRET and ENVIRONMENT unset/development,
    unsigned requests were accepted and could submit goals / resolve approvals."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.integrations import router

    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(path, content=b"text=deploy+prod&user_id=U1")
    assert r.status_code == 503
