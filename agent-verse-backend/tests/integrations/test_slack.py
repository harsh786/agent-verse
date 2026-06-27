"""Tests for Slack integration."""
import hashlib
import hmac
import os
import time

import pytest

from app.integrations.slack.handler import verify_slack_signature


def test_verify_slack_signature_valid():
    secret = "test-secret"
    timestamp = str(int(time.time()))
    body = b"payload=test"
    base = f"v0:{timestamp}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    assert verify_slack_signature(body, timestamp, sig, secret) is True


def test_verify_slack_signature_invalid():
    assert verify_slack_signature(b"body", "ts", "wrong", "secret") is False


def test_verify_slack_signature_no_secret():
    # Empty secret = disabled (return True)
    assert verify_slack_signature(b"body", "ts", "anything", "") is True


def test_verify_slack_signature_stale():
    secret = "s"
    old_ts = str(int(time.time()) - 400)  # > 5 min old
    assert verify_slack_signature(b"body", old_ts, "v0=xxx", secret) is False


@pytest.mark.asyncio
async def test_slack_command_endpoint_rejects_bad_sig():
    from unittest.mock import patch

    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "real-secret"}):
            r = await c.post(
                "/integrations/slack/commands",
                content=b"command=/agentverse&text=test",
                headers={
                    "X-Slack-Signature": "v0=bad",
                    "X-Slack-Request-Timestamp": str(int(time.time())),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_zapier_trigger_without_secret():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/integrations/zapier/trigger",
            json={"goal": "process the data"},
        )
        assert r.status_code in (200, 503)  # 200 if service available, 503 if not
