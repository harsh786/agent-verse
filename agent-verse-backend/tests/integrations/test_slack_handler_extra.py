"""Extra coverage for app/integrations/slack/handler.py."""
from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.slack.handler import (
    get_slack_bot_token,
    get_slack_signing_secret,
    send_approval_request_to_slack,
    send_slack_message,
    verify_slack_signature,
)


class TestGetSlackConfig:
    def test_signing_secret_from_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "my-secret")
        assert get_slack_signing_secret() == "my-secret"

    def test_signing_secret_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        assert get_slack_signing_secret() == ""

    def test_bot_token_from_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        assert get_slack_bot_token() == "xoxb-test"

    def test_bot_token_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        assert get_slack_bot_token() == ""


class TestVerifySlackSignature:
    def test_no_secret_dev_mode_allows(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        result = verify_slack_signature(b"body", "12345", "v0=sig", "")
        assert result is True

    def test_no_secret_production_denies(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        result = verify_slack_signature(b"body", "12345", "v0=sig", "")
        assert result is False

    def test_stale_timestamp_rejected(self):
        old_ts = str(int(time.time()) - 400)  # >5 min old
        result = verify_slack_signature(b"body", old_ts, "v0=sig", "my-secret")
        assert result is False

    def test_invalid_timestamp_rejected(self):
        result = verify_slack_signature(b"body", "not-a-number", "v0=sig", "my-secret")
        assert result is False

    def test_valid_signature_accepted(self):
        import hashlib
        import hmac as hmac_mod
        ts = str(int(time.time()))
        body = b"payload=test"
        secret = "test-secret"
        base = f"v0:{ts}:{body.decode('utf-8')}"
        expected = "v0=" + hmac_mod.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
        result = verify_slack_signature(body, ts, expected, secret)
        assert result is True

    def test_wrong_signature_rejected(self):
        ts = str(int(time.time()))
        result = verify_slack_signature(b"body", ts, "v0=wrongsignature", "my-secret")
        assert result is False


class TestSendSlackMessage:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_token(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        result = await send_slack_message("C123", "Hello")
        assert result["ok"] is False
        assert "SLACK_BOT_TOKEN" in result["error"]

    @pytest.mark.asyncio
    async def test_sends_message_with_token(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "ts": "1234567890.123"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await send_slack_message("C123", "Test message")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_sends_message_with_blocks(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "test"}}]
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await send_slack_message("C123", "fallback", blocks=blocks)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await send_slack_message("C123", "Test")
        assert result["ok"] is False
        assert "error" in result


class TestSendApprovalRequestToSlack:
    @pytest.mark.asyncio
    async def test_sends_to_default_channel(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.delenv("SLACK_APPROVAL_CHANNEL", raising=False)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await send_approval_request_to_slack(
                request_id="req1",
                goal_id="goal1",
                action="deploy to prod",
                risk_level="high",
            )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_sends_to_specified_channel(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await send_approval_request_to_slack(
                request_id="req1",
                goal_id="goal1",
                action="delete db",
                risk_level="critical",
                channel="#my-channel",
            )
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1] if call_args.args else {}
        if isinstance(payload, dict):
            assert payload.get("channel") == "#my-channel"
