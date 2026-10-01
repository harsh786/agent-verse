"""TRG-02: /agentverse routes by the verified workspace -> tenant binding.

The slash command used to submit every workspace's goals into the single tenant
named by SLACK_TENANT_ID. The tenant now comes from the workspace's verified
``channel_tenant_mappings`` row; an unbound workspace is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router as integrations_router

_SECRET = "trg02-slack-secret"


@pytest.fixture(autouse=True)
def _slack_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SECRET)
    monkeypatch.setenv("SLACK_TENANT_ID", "env-tenant-must-be-ignored")


def _signed(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(
        _SECRET.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
    }


def _app(goal_service: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router)
    app.state.settings = SimpleNamespace(slack_tenant_id="settings-tenant-must-be-ignored")
    app.state.goal_service = goal_service
    return app


async def _resolve(channel_type: str, channel_id: str, db: object) -> str | None:
    if channel_type != "slack":
        return None
    return {"T-A": "tenant-a", "T-B": "tenant-b"}.get(channel_id)


def _post(app: FastAPI, team_id: str, user_id: str = "U1") -> Any:
    body = urllib.parse.urlencode(
        {"text": "summarise the incident", "user_id": user_id, "team_id": team_id}
    ).encode()
    with patch("app.api.channels.ingestion._resolve_tenant_from_channel", _resolve):
        return TestClient(app).post(
            "/integrations/slack/commands", content=body, headers=_signed(body)
        )


def test_command_from_workspace_b_submits_into_tenant_b() -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-b"})
    resp = _post(_app(svc), "T-B")
    assert resp.status_code == 200
    svc.submit_goal.assert_awaited_once()
    assert svc.submit_goal.await_args.kwargs["tenant_ctx"].tenant_id == "tenant-b"


def test_command_from_workspace_a_submits_into_tenant_a() -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-a"})
    _post(_app(svc), "T-A")
    assert svc.submit_goal.await_args.kwargs["tenant_ctx"].tenant_id == "tenant-a"


def test_unbound_workspace_is_refused_and_submits_nothing() -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-x"})
    resp = _post(_app(svc), "T-STRANGER")
    assert resp.status_code == 200  # Slack shows the ephemeral text
    data = resp.json()
    assert data["response_type"] == "ephemeral"
    assert "not linked" in data["text"].lower()
    svc.submit_goal.assert_not_awaited()


def test_missing_team_id_is_refused() -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-x"})
    resp = _post(_app(svc), "")
    assert resp.json()["response_type"] == "ephemeral"
    svc.submit_goal.assert_not_awaited()
