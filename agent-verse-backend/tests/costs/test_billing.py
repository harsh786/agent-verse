"""Tests for billing webhook security — secret enforcement and HMAC validation."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    from app.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_webhook_returns_503_when_secret_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    response = client.post(
        "/billing/webhook",
        content=b'{"event": "payment.captured"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 503


def test_webhook_rejects_invalid_signature(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "test-secret")
    response = client.post(
        "/billing/webhook",
        content=b'{"event": "payment.captured"}',
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "invalid-sig",
        },
    )
    assert response.status_code in (400, 401, 422)


def test_webhook_accepts_valid_signature(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import hmac

    secret = "test-secret"
    body = b'{"event": "unknown.event"}'
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/billing/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
        },
    )
    assert response.status_code == 200
