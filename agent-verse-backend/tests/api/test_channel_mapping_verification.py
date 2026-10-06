"""TRG-03: channel ownership is proven from the channel itself.

``POST /channels/mappings`` used to be first-come: any tenant could claim another
tenant's Slack workspace / Discord guild / number and receive its inbound events.
A mapping is now created ``pending_verification`` with a short one-time code
(hashed at rest, 24h TTL); it routes nothing until an inbound message on that
channel carries the code. These unit tests cover the code helpers and the route
wiring; the SQL state machine is exercised against real Postgres in
``test_channel_mapping_verification_integration.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.channels import verification
from app.api.channels.ingestion import router

# ── code helpers ──────────────────────────────────────────────────────────


def test_generated_code_is_short_random_and_unambiguous() -> None:
    codes = {verification.generate_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"AV-[A-HJ-NP-Z2-9]{8}", code), code


def test_hash_is_channel_bound_and_never_the_code() -> None:
    code = "AV-ABCD2345"
    h = verification.hash_code("slack", "T1", code)
    assert code not in h and "ABCD2345" not in h
    assert h == verification.hash_code("slack", "T1", code.lower())
    assert h != verification.hash_code("slack", "T2", code)
    assert h != verification.hash_code("discord", "T1", code)


def test_extract_codes_scans_nested_payload_case_insensitively() -> None:
    payload = {
        "event": {"text": "please verify av-abcd2345 thanks", "blocks": [{"t": "AV-WXYZ6789"}]}
    }
    assert verification.extract_codes(payload) == ["AV-ABCD2345", "AV-WXYZ6789"]


@pytest.mark.parametrize(
    "text",
    ["no code here", "AV-ABCD234", "XAV-ABCD2345", "AV-ABCD23456", "AV-ABCD0O11"],
)
def test_extract_codes_ignores_lookalikes(text: str) -> None:
    assert verification.extract_codes({"text": text}) == []


def test_extract_codes_is_bounded() -> None:
    text = " ".join(f"AV-ABCDEFG{c}" for c in "ABCDEFGHJK")
    assert len(verification.extract_codes({"text": text})) == verification.MAX_CODES_PER_MESSAGE


# ── routes ────────────────────────────────────────────────────────────────

_SLACK_SECRET = "slack-signing-secret"


def _slack_headers(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(
        _SLACK_SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return {
        "X-Slack-Signature": sig,
        "X-Slack-Request-Timestamp": ts,
        "Content-Type": "application/json",
    }


def _app(*, db: Any = object(), tenant: str | None = "t1") -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.slack_signing_secret = _SLACK_SECRET
    app.state.db = db

    if tenant is not None:

        @app.middleware("http")
        async def inject_tenant(req: Any, call_next: Any) -> Any:
            req.state.tenant = SimpleNamespace(tenant_id=tenant, plan="free")
            return await call_next(req)

    return app, gateway


def test_inbound_message_carrying_a_code_is_consumed_not_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = AsyncMock(return_value="t1")
    resolve = AsyncMock(return_value="t1")
    monkeypatch.setattr(verification, "verify_from_inbound", verify)
    monkeypatch.setattr("app.api.channels.ingestion._resolve_tenant_from_channel", resolve)
    app, gateway = _app()
    body = json.dumps(
        {"type": "event_callback", "team_id": "T1", "event": {"text": "AV-ABCD2345"}}
    ).encode()

    resp = TestClient(app).post("/channels/slack/events", content=body, headers=_slack_headers(body))

    assert resp.status_code == 200
    assert verify.await_args.args[1:3] == ("slack", "T1")
    gateway.ingest.assert_not_called()


def test_inbound_message_without_a_matching_claim_routes_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification, "verify_from_inbound", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.api.channels.ingestion._resolve_tenant_from_channel", AsyncMock(return_value="t1")
    )
    app, gateway = _app()
    body = json.dumps({"type": "event_callback", "team_id": "T1", "event": {"text": "hi"}}).encode()

    resp = TestClient(app).post("/channels/slack/events", content=body, headers=_slack_headers(body))

    assert resp.status_code == 200
    assert gateway.ingest.await_args.kwargs["tenant_id"] == "t1"


def test_create_mapping_returns_pending_with_one_time_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = verification.IssuedMapping(
        id="m1",
        channel_type="slack",
        channel_id="T1",
        status=verification.STATUS_PENDING,
        verification_code="AV-ABCD2345",
        verification_expires_at="2026-10-01T00:00:00+00:00",
    )
    claim = AsyncMock(return_value=issued)
    monkeypatch.setattr(verification, "claim_channel", claim)
    app, _ = _app()

    resp = TestClient(app).post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T1"}
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "pending_verification"
    assert data["verification_code"] == "AV-ABCD2345"
    assert "AV-ABCD2345" in data["instructions"]
    assert claim.await_args.kwargs["tenant_id"] == "t1"


def test_create_mapping_on_a_channel_verified_by_another_tenant_is_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verification,
        "claim_channel",
        AsyncMock(side_effect=verification.ChannelClaimedError("slack", "T1")),
    )
    app, _ = _app()
    resp = TestClient(app).post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T1"}
    )
    assert resp.status_code == 409
    assert "already" in resp.json()["detail"].lower()


def test_create_mapping_without_a_database_fails_closed() -> None:
    # Used to answer {"status": "mapped"} while storing nothing.
    app, _ = _app(db=None)
    resp = TestClient(app).post(
        "/channels/mappings", json={"channel_type": "slack", "channel_id": "T1"}
    )
    assert resp.status_code == 503


def test_verify_action_reissues_a_code(monkeypatch: pytest.MonkeyPatch) -> None:
    issued = verification.IssuedMapping(
        id="m1",
        channel_type="slack",
        channel_id="T1",
        status=verification.STATUS_LEGACY,
        verification_code="AV-WXYZ6789",
        verification_expires_at="2026-10-01T00:00:00+00:00",
    )
    reissue = AsyncMock(return_value=issued)
    monkeypatch.setattr(verification, "reissue_code", reissue)
    app, _ = _app()
    resp = TestClient(app).post("/channels/mappings/m1/verify")
    assert resp.status_code == 200
    assert resp.json()["verification_code"] == "AV-WXYZ6789"
    assert resp.json()["status"] == "legacy_unverified"
    assert reissue.await_args.kwargs["tenant_id"] == "t1"
    assert reissue.await_args.kwargs["mapping_id"] == "m1"


def test_verify_action_unknown_mapping_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verification, "reissue_code", AsyncMock(side_effect=verification.MappingNotFoundError())
    )
    app, _ = _app()
    assert TestClient(app).post("/channels/mappings/nope/verify").status_code == 404


def test_verify_action_requires_auth() -> None:
    app, _ = _app(tenant=None)
    assert TestClient(app).post("/channels/mappings/m1/verify").status_code == 401


def test_list_rejects_unknown_status_filter() -> None:
    app, _ = _app()
    assert TestClient(app).get("/channels/mappings?status=bogus").status_code == 422


def test_routing_lookup_only_considers_routable_statuses() -> None:
    assert set(verification.ROUTABLE_STATUSES) == {"verified", "legacy_unverified"}
    assert verification.STATUS_PENDING not in verification.ROUTABLE_STATUSES


# ── operator approval for channels where sending proves nothing ───────────
# A one-time code received on an SMS number / email address (or a public form,
# or a meeting's participants) only proves someone can SEND there, not that
# they own it. Such claims wait for a platform operator.


@pytest.mark.parametrize("channel_type", ["sms", "email", "form", "meeting", "voice", "SMS"])
def test_send_only_channels_need_operator_approval(channel_type: str) -> None:
    assert verification.requires_operator_approval(channel_type)


@pytest.mark.parametrize("channel_type", ["slack", "teams", "discord"])
def test_app_install_channels_are_verified_by_code(channel_type: str) -> None:
    assert not verification.requires_operator_approval(channel_type)


def test_new_statuses_never_route() -> None:
    for status in (
        verification.STATUS_PENDING_OPERATOR,
        verification.STATUS_SUPERSEDED,
        verification.STATUS_REJECTED,
    ):
        assert status in verification.ALL_STATUSES
        assert status not in verification.ROUTABLE_STATUSES


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_type", ["sms", "email"])
async def test_a_code_on_a_send_only_channel_verifies_nothing(channel_type: str) -> None:
    def _no_db() -> Any:
        raise AssertionError("a send-only channel must not even look for a claim")

    got = await verification.verify_from_inbound(
        _no_db, channel_type, "+15550100", {"text": "AV-ABCD2345"}
    )
    assert got is None


def test_pending_operator_instructions_say_awaiting_operator_approval() -> None:
    issued = verification.IssuedMapping(
        "m1", "sms", "+15550100", verification.STATUS_PENDING_OPERATOR, None, None
    )
    text = issued.to_response()["instructions"]
    assert "awaiting operator approval" in text.lower()
    assert "not routed" in text.lower()


def test_verify_action_on_a_send_only_channel_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verification,
        "reissue_code",
        AsyncMock(side_effect=verification.OperatorApprovalRequiredError("sms")),
    )
    app, _ = _app()
    resp = TestClient(app).post("/channels/mappings/m1/verify")
    assert resp.status_code == 409
    assert "operator approval" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "status", ["pending_operator_approval", "superseded", "rejected", "legacy_unverified"]
)
def test_list_accepts_the_new_status_filters(status: str) -> None:
    app, _ = _app(db=None)
    assert TestClient(app).get(f"/channels/mappings?status={status}").status_code == 200


def _admin_client(monkeypatch: pytest.MonkeyPatch, key: str | None) -> TestClient:
    from app.api.admin import router as admin_router

    if key is None:
        monkeypatch.delenv("PLATFORM_ADMIN_KEY", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_ADMIN_KEY", key)
    app = FastAPI()
    app.include_router(admin_router)
    app.state.system_db_session_factory = None
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/channel-mappings/review"),
        ("post", "/admin/channel-mappings/m1/approve"),
        ("post", "/admin/channel-mappings/m1/reject"),
    ],
)
def test_operator_review_routes_require_the_admin_key(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    client = _admin_client(monkeypatch, "k" * 32)
    assert getattr(client, method)(path).status_code == 403  # QA-6: 403, never 401
    assert getattr(client, method)(path, headers={"X-Admin-Key": "wrong"}).status_code == 403
    unconfigured = _admin_client(monkeypatch, None)
    assert getattr(unconfigured, method)(path, headers={"X-Admin-Key": "x"}).status_code == 503


def test_operator_review_without_the_maintenance_db_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _admin_client(monkeypatch, "k" * 32)
    headers = {"X-Admin-Key": "k" * 32}
    assert client.get("/admin/channel-mappings/review", headers=headers).status_code == 503
    assert client.post("/admin/channel-mappings/m1/approve", headers=headers).status_code == 503
    assert client.post("/admin/channel-mappings/m1/reject", headers=headers).status_code == 503
