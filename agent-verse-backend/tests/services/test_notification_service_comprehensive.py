"""Comprehensive tests for app/services/notification_service.py — targeting 90%+ coverage."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.services.notification_service import NotificationChannel, NotificationService


def _slack_channel(
    channel_id: str = "ch-1",
    tenant_id: str = "t1",
    webhook_url: str = "https://hooks.slack.com/test",
    enabled: bool = True,
) -> NotificationChannel:
    return NotificationChannel(
        channel_id=channel_id,
        tenant_id=tenant_id,
        channel_type="slack",
        config={"webhook_url": webhook_url},
        enabled=enabled,
    )


def _webhook_channel(
    channel_id: str = "ch-2",
    tenant_id: str = "t1",
    url: str = "https://webhook.example.com/notify",
    enabled: bool = True,
    channel_type: str = "webhook",
) -> NotificationChannel:
    return NotificationChannel(
        channel_id=channel_id,
        tenant_id=tenant_id,
        channel_type=channel_type,
        config={"url": url},
        enabled=enabled,
    )


# ── NotificationChannel ───────────────────────────────────────────────────────

class TestNotificationChannel:
    def test_defaults(self) -> None:
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="slack"
        )
        assert ch.enabled is True
        assert ch.config == {}

    def test_disabled_channel(self) -> None:
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="slack", enabled=False
        )
        assert ch.enabled is False


# ── NotificationService — channel management ──────────────────────────────────

class TestNotificationServiceChannelManagement:
    def test_add_channel(self) -> None:
        svc = NotificationService()
        ch = _slack_channel()
        svc.add_channel(ch)
        channels = svc.get_channels("t1")
        assert len(channels) == 1
        assert channels[0].channel_id == "ch-1"

    def test_get_channels_filters_disabled(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel(channel_id="enabled"))
        svc.add_channel(_slack_channel(channel_id="disabled", enabled=False))
        channels = svc.get_channels("t1")
        assert len(channels) == 1
        assert channels[0].channel_id == "enabled"

    def test_get_channels_empty_for_unknown_tenant(self) -> None:
        svc = NotificationService()
        assert svc.get_channels("unknown") == []

    def test_remove_channel_returns_true(self) -> None:
        svc = NotificationService()
        ch = _slack_channel()
        svc.add_channel(ch)
        removed = svc.remove_channel("ch-1", "t1")
        assert removed is True
        assert len(svc.get_channels("t1")) == 0

    def test_remove_channel_unknown_returns_false(self) -> None:
        svc = NotificationService()
        removed = svc.remove_channel("nonexistent", "t1")
        assert removed is False

    def test_multiple_tenants_isolated(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel(channel_id="c1", tenant_id="t1"))
        svc.add_channel(_slack_channel(channel_id="c2", tenant_id="t2"))
        assert len(svc.get_channels("t1")) == 1
        assert len(svc.get_channels("t2")) == 1

    def test_set_db(self) -> None:
        svc = NotificationService()
        mock_db = MagicMock()
        svc.set_db(mock_db)
        assert svc._db == mock_db


# ── notify_approval_required ──────────────────────────────────────────────────

class TestNotifyApprovalRequired:
    async def test_no_channels_returns_sent_zero(self) -> None:
        svc = NotificationService()
        result = await svc.notify_approval_required(
            request_id="r1", goal_id="g1", action="deploy",
            risk_level="high", tenant_id="t1",
        )
        assert result["sent"] == 0
        assert result["channels"] == []

    async def test_slack_channel_sends_notification(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="deploy",
                risk_level="high", tenant_id="t1",
            )

        assert result["sent"] == 1
        assert result["channels"][0]["status"] == "sent"

    async def test_webhook_channel_sends_notification(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r2", goal_id="g2", action="delete",
                risk_level="medium", tenant_id="t1",
            )

        assert result["sent"] == 1

    async def test_teams_channel_sends_notification(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel(channel_id="ch-teams", channel_type="teams"))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r3", goal_id="g3", action="act",
                risk_level="low", tenant_id="t1",
            )

        assert result["sent"] == 1

    async def test_channel_send_failure_tracked(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel())

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.NetworkError("connection refused")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="deploy",
                risk_level="high", tenant_id="t1",
            )

        assert result["sent"] == 0
        assert result["channels"][0]["status"] == "failed"
        assert "error" in result["channels"][0]

    async def test_multiple_channels_partial_success(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel(channel_id="ok"))
        svc.add_channel(_slack_channel(channel_id="fail", webhook_url="https://fail.example.com"))

        call_count = 0

        async def post_side_effect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "fail" in url:
                raise httpx.NetworkError("Fail channel")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.post = post_side_effect

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc.notify_approval_required(
                request_id="r1", goal_id="g1", action="act",
                risk_level="high", tenant_id="t1",
            )

        assert result["sent"] == 1


# ── notify_goal_complete ──────────────────────────────────────────────────────

class TestNotifyGoalComplete:
    async def test_notify_complete_status(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc.notify_goal_complete(goal_id="g1", status="complete", tenant_id="t1")

        mock_client.post.assert_called_once()

    async def test_notify_failed_status(self) -> None:
        svc = NotificationService()
        svc.add_channel(_webhook_channel())

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc.notify_goal_complete(goal_id="g1", status="failed", tenant_id="t1")

        mock_client.post.assert_called_once()

    async def test_notify_no_channels_noop(self) -> None:
        svc = NotificationService()
        await svc.notify_goal_complete(goal_id="g1", status="complete", tenant_id="t1")

    async def test_send_error_is_logged_not_raised(self) -> None:
        svc = NotificationService()
        svc.add_channel(_slack_channel())

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                side_effect=httpx.NetworkError("err")
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            # Must not raise
            await svc.notify_goal_complete(goal_id="g1", status="failed", tenant_id="t1")


# ── _send (internal routing) ──────────────────────────────────────────────────

class TestSendInternal:
    async def test_slack_no_webhook_url_noop(self) -> None:
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="slack",
            config={}  # no webhook_url
        )
        await svc._send(ch, {"text": "test"})  # no exception, no HTTP call

    async def test_webhook_no_url_noop(self) -> None:
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="webhook",
            config={}  # no url
        )
        await svc._send(ch, {"type": "test"})  # no exception

    async def test_unknown_channel_type_noop(self) -> None:
        svc = NotificationService()
        ch = NotificationChannel(
            channel_id="c1", tenant_id="t1", channel_type="pagerduty",
            config={"key": "val"}
        )
        await svc._send(ch, {"type": "test"})  # no exception


# ── sync_from_db ──────────────────────────────────────────────────────────────

class TestSyncFromDb:
    async def test_sync_no_db_noop(self) -> None:
        svc = NotificationService()
        await svc.sync_from_db()  # no exception

    async def test_sync_from_db_loads_channels(self) -> None:
        rows = [("ch-db", "t1", "slack", {"webhook_url": "https://h.slack.com"}, True)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        svc = NotificationService()
        svc.set_db(factory)
        await svc.sync_from_db()
        channels = svc.get_channels("t1")
        assert len(channels) == 1
        assert channels[0].channel_id == "ch-db"

    async def test_sync_from_db_deduplicates(self) -> None:
        rows = [("ch-db", "t1", "slack", {"webhook_url": "https://s.com"}, True)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        @asynccontextmanager
        async def factory():
            yield mock_session

        svc = NotificationService()
        svc.set_db(factory)
        await svc.sync_from_db()
        await svc.sync_from_db()  # second sync should not duplicate
        channels = svc.get_channels("t1")
        assert len(channels) == 1

    async def test_sync_from_db_error_suppressed(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB error"))

        @asynccontextmanager
        async def factory():
            yield mock_session

        svc = NotificationService()
        svc.set_db(factory)
        await svc.sync_from_db()  # must not raise
