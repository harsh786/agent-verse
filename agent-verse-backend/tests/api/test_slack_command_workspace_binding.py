"""DEF-2: Slack slash commands / interactions are bound by the VERIFIED team id.

The request must carry a valid ``v0`` signature (HMAC over
``v0:{timestamp}:{body}``, 5-minute window) made with either the platform Slack
app's signing secret or the signing secret of the tenant's own Slack app bound
to the workspace the body names. The goal goes to the tenant bound to that
workspace; an unbound workspace gets an honest error and nothing is submitted.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router
from app.gateway.channel_registry import ChannelRegistry

# Built from parts so no signing-secret-shaped literal sits in the repo.
_SECRET_A = "-".join(("tenant", "a", "slack", "signing"))
_SECRET_B = "-".join(("tenant", "b", "slack", "signing"))
_PLATFORM = "-".join(("platform", "slack", "signing"))


def _sign(body: bytes, secret: str, ts: int | None = None) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    mac = hmac.new(secret.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256)
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": stamp,
        "X-Slack-Signature": "v0=" + mac.hexdigest(),
    }


def _command(team_id: str, text: str = "summarise the incident") -> bytes:
    return urllib.parse.urlencode(
        {"command": "/agentverse", "text": text, "user_id": "U1", "team_id": team_id}
    ).encode()


@pytest.fixture(autouse=True)
def _mappings_and_principals() -> Iterator[None]:
    bound = {"TAAAA1": "tenant-a", "TBBBB2": "tenant-b"}

    async def _resolve(channel_type: str, channel_id: str, db: object) -> str | None:
        return bound.get(channel_id) if channel_type == "slack" else None

    from app.integrations.slack.identity import SlackPrincipal

    async def _principal(*, system_db: Any, tenant_id: str, team_id: str,
                         slack_user_id: str) -> Any:
        return SlackPrincipal(tenant_id=tenant_id, principal_id="key-1", roles=("admin",))

    with (
        patch("app.api.channels.ingestion._resolve_tenant_from_channel", _resolve),
        patch("app.integrations.slack.identity.resolve_slack_principal", _principal),
    ):
        yield


def _app(monkeypatch: pytest.MonkeyPatch, *, platform: str = "") -> tuple[TestClient, Any]:
    if platform:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", platform)
    else:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("SLACK_TENANT_ID", "env-tenant-must-be-ignored")
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace()
    registry = ChannelRegistry()
    registry.register("slack", "TAAAA1", "tenant-a", secret=_SECRET_A)
    registry.register("slack", "TBBBB2", "tenant-b", secret=_SECRET_B)
    app.state.channel_registry = registry
    goals = MagicMock()
    goals.submit_goal = AsyncMock(return_value={"goal_id": "g-1"})
    app.state.goal_service = goals
    return TestClient(app), goals


def test_command_signed_by_the_workspaces_own_app_reaches_its_tenant(monkeypatch) -> None:
    client, goals = _app(monkeypatch)
    body = _command("TAAAA1")
    resp = client.post("/integrations/slack/commands", content=body, headers=_sign(body, _SECRET_A))
    assert resp.status_code == 200, resp.text
    assert resp.json()["response_type"] == "in_channel"
    assert goals.submit_goal.await_args.kwargs["tenant_ctx"].tenant_id == "tenant-a"


def test_platform_signed_command_goes_to_the_bound_tenant_not_env(monkeypatch) -> None:
    client, goals = _app(monkeypatch, platform=_PLATFORM)
    body = _command("TBBBB2")
    resp = client.post("/integrations/slack/commands", content=body, headers=_sign(body, _PLATFORM))
    assert resp.status_code == 200
    assert goals.submit_goal.await_args.kwargs["tenant_ctx"].tenant_id == "tenant-b"


def test_one_workspaces_secret_cannot_speak_for_another_workspace(monkeypatch) -> None:
    """Tenant A holds its own signing secret; naming tenant B's team id selects
    B's binding, whose secret A's signature does not match."""
    client, goals = _app(monkeypatch, platform=_PLATFORM)
    body = _command("TBBBB2")
    resp = client.post("/integrations/slack/commands", content=body, headers=_sign(body, _SECRET_A))
    assert resp.status_code == 403
    goals.submit_goal.assert_not_awaited()


def test_stale_timestamp_is_refused(monkeypatch) -> None:
    client, goals = _app(monkeypatch)
    body = _command("TAAAA1")
    headers = _sign(body, _SECRET_A, ts=int(time.time()) - 301)
    assert client.post("/integrations/slack/commands", content=body, headers=headers).status_code == 403
    goals.submit_goal.assert_not_awaited()


def test_unbound_workspace_gets_an_honest_error(monkeypatch) -> None:
    client, goals = _app(monkeypatch, platform=_PLATFORM)
    body = _command("TZZZZ9")
    resp = client.post("/integrations/slack/commands", content=body, headers=_sign(body, _PLATFORM))
    assert resp.status_code == 200
    data = resp.json()
    assert data["response_type"] == "ephemeral"
    assert "not linked to an agentverse tenant" in data["text"].lower()
    goals.submit_goal.assert_not_awaited()


def test_no_platform_secret_and_no_binding_is_503(monkeypatch) -> None:
    client, goals = _app(monkeypatch)
    body = _command("TZZZZ9")
    resp = client.post("/integrations/slack/commands", content=body, headers=_sign(body, "x"))
    assert resp.status_code == 503
    goals.submit_goal.assert_not_awaited()


def test_binding_store_outage_is_503(monkeypatch) -> None:
    from app.gateway.binding_store import ChannelBindingStoreUnavailableError

    client, goals = _app(monkeypatch)
    body = _command("TAAAA1")
    with patch(
        "app.gateway.binding_store.resolve_binding",
        AsyncMock(side_effect=ChannelBindingStoreUnavailableError("db down")),
    ):
        resp = client.post(
            "/integrations/slack/commands", content=body, headers=_sign(body, _SECRET_A)
        )
    assert resp.status_code == 503
    goals.submit_goal.assert_not_awaited()


def test_non_utf8_body_is_refused_not_a_500(monkeypatch) -> None:
    client, goals = _app(monkeypatch, platform=_PLATFORM)
    body = b"team_id=TAAAA1&text=\xff\xfe"
    headers = _sign(body, "wrong")
    assert client.post("/integrations/slack/commands", content=body, headers=headers).status_code == 403


def test_hitl_interaction_signed_by_bound_app_is_accepted(monkeypatch) -> None:
    import json

    client, _ = _app(monkeypatch)
    payload = {"type": "block_actions", "team": {"id": "TAAAA1"}, "user": {"id": "U1"},
               "actions": []}
    body = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()
    resp = client.post("/integrations/slack/events", content=body, headers=_sign(body, _SECRET_A))
    assert resp.status_code == 200
    forged = client.post(
        "/integrations/slack/events", content=body, headers=_sign(body, _SECRET_B)
    )
    assert forged.status_code == 403
