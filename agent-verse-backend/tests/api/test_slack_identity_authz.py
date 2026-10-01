"""TRG-36: Slack actions need a linked AgentVerse principal with the right scope.

Any member of a bound workspace could approve/reject HITL requests (and submit
goals) — nothing tied the Slack user to an AgentVerse identity or role. A Slack
user now acts only through a linked principal whose live API key grants
``governance:approve`` (decisions) or ``goals:write`` (goals); the decision
records that principal as the approver.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router as integrations_router
from app.integrations.slack.identity import (
    SlackIdentityStoreUnavailableError,
    SlackPrincipal,
)

_SECRET = "trg36-slack-secret"
_TENANT = "tenant-bound"


@pytest.fixture(autouse=True)
def _slack_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SECRET)


def _headers(body: bytes, content_type: str) -> dict[str, str]:
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body.decode()}".encode()
    sig = "v0=" + hmac.new(_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {
        "Content-Type": content_type,
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
    }


def _app(**state: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router)
    app.state.settings = SimpleNamespace()
    for k, v in state.items():
        setattr(app.state, k, v)
    return app


async def _bound(channel_type: str, channel_id: str, db: object) -> str | None:
    return _TENANT if channel_id == "T1" else None


_LINKS: dict[tuple[str, str, str], SlackPrincipal] = {}


async def _resolve(*, system_db: Any, tenant_id: str, team_id: str, slack_user_id: str) -> Any:
    return _LINKS.get((tenant_id, team_id, slack_user_id))


@pytest.fixture
def links() -> Iterator[dict[tuple[str, str, str], SlackPrincipal]]:
    _LINKS.clear()
    with (
        patch("app.api.channels.ingestion._resolve_tenant_from_channel", _bound),
        patch("app.integrations.slack.identity.resolve_slack_principal", _resolve),
    ):
        yield _LINKS
    _LINKS.clear()


def _approver(key: str = "key-approver") -> SlackPrincipal:
    return SlackPrincipal(tenant_id=_TENANT, principal_id=key, roles=("approver",))


def _operator(key: str = "key-operator") -> SlackPrincipal:
    return SlackPrincipal(tenant_id=_TENANT, principal_id=key, roles=("operator",))


def _block_actions(user: str, action: str = "approve_hitl") -> bytes:
    return json.dumps(
        {
            "type": "block_actions",
            "team": {"id": "T1"},
            "user": {"id": user, "name": user.lower()},
            "actions": [{"action_id": action, "value": "req-1"}],
        }
    ).encode()


def _hitl() -> MagicMock:
    hitl = MagicMock()
    hitl.approve_async = AsyncMock(return_value=True)
    hitl.reject = AsyncMock()
    return hitl


# ── /slack/events block_actions ──────────────────────────────────────────────


def test_unlinked_slack_user_click_is_refused(links: dict[Any, Any]) -> None:
    hitl = _hitl()
    body = _block_actions("U-STRANGER")
    resp = TestClient(_app(hitl_gateway=hitl)).post(
        "/integrations/slack/events", content=body, headers=_headers(body, "application/json")
    )
    assert resp.status_code == 200
    assert resp.json()["response_type"] == "ephemeral"
    assert "not linked" in resp.json()["text"]
    hitl.approve_async.assert_not_awaited()
    hitl.reject.assert_not_awaited()


def test_linked_approver_click_decides_as_the_agentverse_principal(
    links: dict[Any, Any],
) -> None:
    links[(_TENANT, "T1", "U-ALICE")] = _approver("key-alice")
    hitl = _hitl()
    body = _block_actions("U-ALICE")
    resp = TestClient(_app(hitl_gateway=hitl)).post(
        "/integrations/slack/events", content=body, headers=_headers(body, "application/json")
    )
    assert resp.status_code == 200
    hitl.approve_async.assert_awaited_once()
    kwargs = hitl.approve_async.await_args.kwargs
    assert kwargs["approver"] == "key-alice"  # not "slack:U-ALICE"
    assert kwargs["tenant_ctx"].tenant_id == _TENANT
    assert kwargs["tenant_ctx"].api_key_id == "key-alice"


def test_linked_reject_records_the_principal(links: dict[Any, Any]) -> None:
    links[(_TENANT, "T1", "U-ALICE")] = _approver("key-alice")
    hitl = _hitl()
    body = _block_actions("U-ALICE", "reject_hitl")
    TestClient(_app(hitl_gateway=hitl)).post(
        "/integrations/slack/events", content=body, headers=_headers(body, "application/json")
    )
    hitl.reject.assert_awaited_once()
    assert hitl.reject.await_args.kwargs["approver"] == "key-alice"


def test_linked_user_without_approve_scope_is_refused(links: dict[Any, Any]) -> None:
    links[(_TENANT, "T1", "U-OP")] = _operator()
    hitl = _hitl()
    body = _block_actions("U-OP")
    resp = TestClient(_app(hitl_gateway=hitl)).post(
        "/integrations/slack/events", content=body, headers=_headers(body, "application/json")
    )
    assert "governance:approve" in resp.json()["text"]
    hitl.approve_async.assert_not_awaited()


def test_scoped_key_without_approve_scope_is_refused(links: dict[Any, Any]) -> None:
    links[(_TENANT, "T1", "U-ADM")] = SlackPrincipal(
        tenant_id=_TENANT, principal_id="k", roles=("admin",), scopes=("goals:read",)
    )
    hitl = _hitl()
    body = _block_actions("U-ADM")
    TestClient(_app(hitl_gateway=hitl)).post(
        "/integrations/slack/events", content=body, headers=_headers(body, "application/json")
    )
    hitl.approve_async.assert_not_awaited()


def test_identity_store_failure_refuses(links: dict[Any, Any]) -> None:
    async def _boom(**_: Any) -> Any:
        raise SlackIdentityStoreUnavailableError("db down")

    hitl = _hitl()
    body = _block_actions("U-ALICE")
    with patch("app.integrations.slack.identity.resolve_slack_principal", _boom):
        resp = TestClient(_app(hitl_gateway=hitl)).post(
            "/integrations/slack/events",
            content=body,
            headers=_headers(body, "application/json"),
        )
    assert resp.status_code == 200
    assert resp.json()["response_type"] == "ephemeral"
    hitl.approve_async.assert_not_awaited()


# ── /slack/interactive ───────────────────────────────────────────────────────


def _interactive(user: str) -> bytes:
    payload = json.loads(_block_actions(user))
    return ("payload=" + urllib.parse.quote_plus(json.dumps(payload))).encode()


def test_interactive_unlinked_user_resumes_nothing(links: dict[Any, Any]) -> None:
    svc = MagicMock()
    svc.resume_goal = AsyncMock()
    body = _interactive("U-STRANGER")
    resp = TestClient(_app(goal_service=svc)).post(
        "/integrations/slack/interactive",
        content=body,
        headers=_headers(body, "application/x-www-form-urlencoded"),
    )
    assert resp.status_code == 200
    svc.resume_goal.assert_not_awaited()


def test_interactive_linked_approver_resumes_as_principal(links: dict[Any, Any]) -> None:
    links[(_TENANT, "T1", "U-ALICE")] = _approver("key-alice")
    svc = MagicMock()
    svc.resume_goal = AsyncMock()
    body = _interactive("U-ALICE")
    TestClient(_app(goal_service=svc)).post(
        "/integrations/slack/interactive",
        content=body,
        headers=_headers(body, "application/x-www-form-urlencoded"),
    )
    svc.resume_goal.assert_awaited_once()
    kwargs = svc.resume_goal.await_args.kwargs
    assert kwargs["approved"] is True
    assert kwargs["tenant_ctx"].api_key_id == "key-alice"
    assert "key-alice" in kwargs["feedback"]


# ── /slack/commands ──────────────────────────────────────────────────────────


def _command(text: str, user: str) -> bytes:
    return urllib.parse.urlencode({"text": text, "user_id": user, "team_id": "T1"}).encode()


def test_command_from_unlinked_user_submits_nothing(links: dict[Any, Any]) -> None:
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g"})
    body = _command("do things", "U-STRANGER")
    resp = TestClient(_app(goal_service=svc)).post(
        "/integrations/slack/commands",
        content=body,
        headers=_headers(body, "application/x-www-form-urlencoded"),
    )
    assert "not linked" in resp.json()["text"]
    svc.submit_goal.assert_not_awaited()


def test_command_from_linked_operator_submits_as_principal(links: dict[Any, Any]) -> None:
    links[(_TENANT, "T1", "U-OP")] = _operator("key-op")
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-1"})
    body = _command("do things", "U-OP")
    resp = TestClient(_app(goal_service=svc)).post(
        "/integrations/slack/commands",
        content=body,
        headers=_headers(body, "application/x-www-form-urlencoded"),
    )
    assert resp.json()["response_type"] == "in_channel"
    ctx = svc.submit_goal.await_args.kwargs["tenant_ctx"]
    assert ctx.api_key_id == "key-op"
    assert ctx.roles == ("operator",)


def test_command_from_approver_only_principal_is_refused(links: dict[Any, Any]) -> None:
    links[(_TENANT, "T1", "U-APP")] = _approver()
    svc = MagicMock()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g"})
    body = _command("do things", "U-APP")
    resp = TestClient(_app(goal_service=svc)).post(
        "/integrations/slack/commands",
        content=body,
        headers=_headers(body, "application/x-www-form-urlencoded"),
    )
    assert "goals:write" in resp.json()["text"]
    svc.submit_goal.assert_not_awaited()


def test_link_command_redeems_in_the_bound_tenant(links: dict[Any, Any]) -> None:
    calls: list[dict[str, Any]] = []

    async def _redeem(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return kwargs["code"] == "GOODCODE23"

    svc = MagicMock()
    svc.submit_goal = AsyncMock()
    with patch("app.integrations.slack.identity.redeem_link_code", _redeem):
        client = TestClient(_app(goal_service=svc))
        ok = _command("link GOODCODE23", "U-NEW")
        bad = _command("link WRONG", "U-NEW")
        ok_resp = client.post(
            "/integrations/slack/commands",
            content=ok,
            headers=_headers(ok, "application/x-www-form-urlencoded"),
        )
        bad_resp = client.post(
            "/integrations/slack/commands",
            content=bad,
            headers=_headers(bad, "application/x-www-form-urlencoded"),
        )
    assert "now linked" in ok_resp.json()["text"]
    assert "invalid or expired" in bad_resp.json()["text"]
    assert calls[0]["bound_tenant_id"] == _TENANT
    assert calls[0]["team_id"] == "T1"
    assert calls[0]["slack_user_id"] == "U-NEW"
    svc.submit_goal.assert_not_awaited()


# ── principal scopes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_identity_endpoints_are_open_to_approver_keys(method: str) -> None:
    """An approver key (barred from unregistered writes) must be able to link
    its own Slack identity — it is exactly who approves from Slack."""
    from app.auth.scope_enforcement import ROLE_SCOPES, ScopeEnforcementMiddleware

    scope = ScopeEnforcementMiddleware._required_scope(method, "/channels/identities/link-codes")
    assert scope == "goals:read"
    assert all(scope in ROLE_SCOPES[r] for r in ("admin", "operator", "approver", "viewer"))


def test_principal_scopes_follow_the_api_role_table() -> None:
    assert _approver().can("governance:approve")
    assert not _approver().can("goals:write")
    assert _operator().can("goals:write")
    assert not _operator().can("governance:approve")
    admin = SlackPrincipal(tenant_id="t", principal_id="k", roles=("admin",))
    assert admin.can("governance:approve") and admin.can("goals:write")
