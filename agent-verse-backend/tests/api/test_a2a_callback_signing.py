"""A2A-04: A2A callbacks are signed so receivers can authenticate them.

The completion callback was a bare JSON POST: anyone who learned a callback
URL could forge "task complete" results. It now carries the same scheme the
platform requires on inbound tasks: ``X-A2A-Timestamp`` (unix seconds) and
``X-A2A-Signature: sha256=<hex HMAC-SHA256(A2A_SHARED_SECRET,
f"{timestamp}." + raw_body)>``. Delivery reports success only on a 2xx.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

import app.net.ssrf_guard as g
from app.api.a2a import _send_callback, _verify_hmac

URL = "https://receiver.example/cb"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])


async def test_callback_is_signed_and_verifiable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_SHARED_SECRET", "s3cret")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200))
        assert await _send_callback(URL, "task-1", "complete", "the answer") is True
    req = route.calls[0].request
    ts = req.headers["X-A2A-Timestamp"]
    assert abs(int(ts) - time.time()) < 60
    assert _verify_hmac(f"{ts}.".encode() + req.content, req.headers["X-A2A-Signature"], "s3cret")
    body = json.loads(req.content)
    assert body["task_id"] == "task-1" and body["status"] == "complete"
    assert body["result"] == "the answer"


async def test_tampered_body_does_not_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_SHARED_SECRET", "s3cret")
    with respx.mock() as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(204))
        await _send_callback(URL, "task-1", "complete", "x")
    req = route.calls[0].request
    forged = req.content.replace(b'"complete"', b'"failed"')
    assert not _verify_hmac(
        f"{req.headers['X-A2A-Timestamp']}.".encode() + forged,
        req.headers["X-A2A-Signature"],
        "s3cret",
    )


async def test_non_2xx_is_not_reported_as_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_SHARED_SECRET", "s3cret")
    with respx.mock() as mock:
        mock.post(URL).mock(return_value=httpx.Response(500))
        assert await _send_callback(URL, "task-1", "complete", "x") is False


def test_agent_card_documents_the_full_signature_scheme() -> None:
    """A2A-02: the card advertised only X-A2A-Signature, not how to compute it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.a2a import A2A_SIGNATURE_MAX_SKEW_SECONDS, router

    app = FastAPI()
    app.include_router(router)
    auth = TestClient(app).get("/.well-known/agent.json").json()["authentication"]
    assert auth["scheme"] == "hmac-sha256"
    assert auth["header"] == auth["signature_header"] == "X-A2A-Signature"
    assert auth["timestamp_header"] == "X-A2A-Timestamp"
    assert auth["signed_string"] == "{timestamp}.{raw_body}"
    assert auth["signature_format"] == "sha256=<lowercase hex HMAC-SHA256>"
    assert auth["max_clock_skew_seconds"] == A2A_SIGNATURE_MAX_SKEW_SECONDS
    assert auth["replay_protection"]
    assert auth["callbacks"]["signed"] is True


async def test_production_without_a_secret_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    monkeypatch.setattr("app.api.a2a._is_production", lambda: True)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200))
        assert await _send_callback(URL, "task-1", "complete", "x") is False
    assert not route.called
