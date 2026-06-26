"""Tests for app/services/notification_service.py — 8 tests using respx."""
from __future__ import annotations

import pytest
import respx
import httpx

from app.services.notification_service import NotificationChannel, NotificationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> NotificationService:
    return NotificationService()


def _slack_channel(tenant_id: str = "t1", enabled: bool = True) -> NotificationChannel:
    return NotificationChannel(
        channel_id="ch-slack",
        tenant_id=tenant_id,
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/test"},
        enabled=enabled,
    )


def _webhook_channel(tenant_id: str = "t1", url: str = "https://example.com/hook") -> NotificationChannel:
    return NotificationChannel(
        channel_id="ch-webhook",
        tenant_id=tenant_id,
        channel_type="webhook",
        config={"url": url},
    )


def _teams_channel(tenant_id: str = "t1") -> NotificationChannel:
    return NotificationChannel(
        channel_id="ch-teams",
        tenant_id=tenant_id,
        channel_type="teams",
        config={"url": "https://teams.example.com/hook"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@respx.mock
async def test_notify_approval_required_sends_to_slack() -> None:
    """notify_approval_required posts to the Slack webhook URL."""
    route = respx.post("https://hooks.slack.com/test").mock(return_value=httpx.Response(200))

    svc = _make_service()
    svc.add_channel(_slack_channel())

    await svc.notify_approval_required(
        request_id="req1", goal_id="g1", action="delete_db",
        risk_level="high", tenant_id="t1",
    )

    assert route.called


@pytest.mark.anyio
@respx.mock
async def test_notify_approval_required_returns_sent_count() -> None:
    """Returns dict with sent == 1 when channel succeeds."""
    respx.post("https://hooks.slack.com/test").mock(return_value=httpx.Response(200))

    svc = _make_service()
    svc.add_channel(_slack_channel())

    result = await svc.notify_approval_required(
        request_id="req1", goal_id="g1", action="act",
        risk_level="medium", tenant_id="t1",
    )

    assert result["sent"] == 1
    assert result["channels"][0]["status"] == "sent"


@pytest.mark.anyio
async def test_no_channels_returns_zero_sent() -> None:
    """When no channels are configured, sent == 0 and channels list is empty."""
    svc = _make_service()

    result = await svc.notify_approval_required(
        request_id="req1", goal_id="g1", action="act",
        risk_level="low", tenant_id="t1",
    )

    assert result == {"sent": 0, "channels": []}


@pytest.mark.anyio
async def test_disabled_channels_are_skipped() -> None:
    """Disabled channels do not receive notifications."""
    svc = _make_service()
    svc.add_channel(_slack_channel(enabled=False))

    result = await svc.notify_approval_required(
        request_id="req1", goal_id="g1", action="act",
        risk_level="low", tenant_id="t1",
    )

    assert result["sent"] == 0
    assert result["channels"] == []


@pytest.mark.anyio
@respx.mock
async def test_failed_channel_does_not_crash_other_channels() -> None:
    """A channel that raises must not prevent other channels from being notified."""
    respx.post("https://hooks.slack.com/test").mock(side_effect=httpx.ConnectError("down"))
    respx.post("https://example.com/hook").mock(return_value=httpx.Response(200))

    svc = _make_service()
    svc.add_channel(_slack_channel())
    svc.add_channel(_webhook_channel())

    result = await svc.notify_approval_required(
        request_id="req1", goal_id="g1", action="act",
        risk_level="high", tenant_id="t1",
    )

    assert result["sent"] == 1
    statuses = {r["channel_id"]: r["status"] for r in result["channels"]}
    assert statuses["ch-slack"] == "failed"
    assert statuses["ch-webhook"] == "sent"


def test_add_channel_adds_to_tenant() -> None:
    svc = _make_service()
    svc.add_channel(_slack_channel(tenant_id="t2"))

    channels = svc.get_channels("t2")
    assert len(channels) == 1
    assert channels[0].channel_id == "ch-slack"


def test_remove_channel_removes_from_tenant() -> None:
    svc = _make_service()
    svc.add_channel(_slack_channel(tenant_id="t3"))

    removed = svc.remove_channel("ch-slack", "t3")

    assert removed is True
    assert svc.get_channels("t3") == []


@pytest.mark.anyio
@respx.mock
async def test_send_teams_type_works() -> None:
    """_send posts the full message JSON to teams URL."""
    route = respx.post("https://teams.example.com/hook").mock(return_value=httpx.Response(200))

    svc = _make_service()
    svc.add_channel(_teams_channel())

    result = await svc.notify_approval_required(
        request_id="req-t", goal_id="g-t", action="deploy",
        risk_level="medium", tenant_id="t1",
    )

    assert route.called
    assert result["sent"] == 1
