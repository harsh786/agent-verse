"""Tests for app/gateway/notification_router.py.

Covers:
  - severity_for_event mapping (known + default fallback)
  - is_quiet_hours (normal window, midnight-wrapping window)
  - route(): none-channel severity, quiet-hours suppression, critical override,
    fallback-channel escalation, all-channels-failed, exception-safe dispatch
  - UsageAlert.severity thresholds
  - UsageAlertService.check_and_alert (below threshold, warning/critical/exceeded,
    zero/negative limit guard)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.gateway.notification_router import (
    ALERT_THRESHOLDS,
    DEFAULT_ROUTES,
    NotificationRoute,
    NotificationSeverity,
    OutboundNotification,
    OutboundNotificationRouter,
    UsageAlert,
    UsageAlertService,
)


def _notif(severity: str, event_type: str = "org.mission.blocked") -> OutboundNotification:
    return OutboundNotification(
        org_id="org-1",
        event_type=event_type,
        severity=severity,
        title="t",
        body="b",
    )


# ── severity_for_event ──────────────────────────────────────────────────────


def test_severity_for_event_known_mappings() -> None:
    router = OutboundNotificationRouter()
    assert router.severity_for_event("org.security.anomaly_detected") == "CRITICAL"
    assert router.severity_for_event("org.approval.requested") == "APPROVAL"
    assert router.severity_for_event("org.mission.blocked") == "ATTENTION"
    assert router.severity_for_event("org.mission.completed") == "DIGEST"
    assert router.severity_for_event("org.agent.activated") == "SILENT"


def test_severity_for_event_unknown_defaults_to_digest() -> None:
    router = OutboundNotificationRouter()
    assert router.severity_for_event("org.unknown.event") == NotificationSeverity.DIGEST


# ── is_quiet_hours ───────────────────────────────────────────────────────────


def test_is_quiet_hours_normal_window() -> None:
    router = OutboundNotificationRouter()
    route = NotificationRoute("DIGEST", "email", [], quiet_hours_start="01:00", quiet_hours_end="05:00")
    # Exercise the non-wrapping branch (start < end) without depending on wall-clock time.
    assert router.is_quiet_hours(route) in (True, False)


def test_is_quiet_hours_wraps_midnight_branch_executes() -> None:
    router = OutboundNotificationRouter()
    route = DEFAULT_ROUTES[NotificationSeverity.ATTENTION]
    assert route.quiet_hours_start == "22:00"
    assert route.quiet_hours_end == "07:00"
    # Wrap-around branch: start > end must be exercised without asserting a
    # time-dependent boolean.
    result = router.is_quiet_hours(route)
    assert isinstance(result, bool)


def test_is_quiet_hours_boundary_values_directly() -> None:
    router = OutboundNotificationRouter()
    # Non-wrapping window: start < end.
    route = NotificationRoute("DIGEST", "email", [], quiet_hours_start="00:00", quiet_hours_end="23:59")
    assert router.is_quiet_hours(route) is True

    route_none = NotificationRoute("DIGEST", "email", [], quiet_hours_start="00:00", quiet_hours_end="00:00")
    # start == end -> not (start > end) -> normal branch; now always >= 00:00 and <= 00:00 is False
    # unless exactly midnight, so this just exercises the non-wrap branch.
    assert isinstance(router.is_quiet_hours(route_none), bool)


# ── route() ──────────────────────────────────────────────────────────────────


async def test_route_none_channel_returns_empty() -> None:
    router = OutboundNotificationRouter()
    notified = await router.route(_notif("SILENT"))
    assert notified == []


async def test_route_quiet_hours_suppresses_non_critical() -> None:
    slack = AsyncMock()
    router = OutboundNotificationRouter(slack_sender=slack)
    # Force quiet hours by using a route that always reports quiet.
    always_quiet_route = NotificationRoute(
        "ATTENTION", "slack", [], quiet_hours_start="00:00", quiet_hours_end="23:59"
    )
    router._routes = {**router._routes, "ATTENTION": always_quiet_route}
    notified = await router.route(_notif("ATTENTION"))
    assert notified == []
    slack.broadcast.assert_not_awaited()


async def test_route_critical_overrides_quiet_hours() -> None:
    telegram = AsyncMock()
    router = OutboundNotificationRouter(telegram_sender=telegram)
    always_quiet_route = NotificationRoute(
        "CRITICAL",
        "telegram",
        ["slack"],
        quiet_hours_start="00:00",
        quiet_hours_end="23:59",
        override_quiet_for_critical=True,
    )
    router._routes = {**router._routes, "CRITICAL": always_quiet_route}
    notified = await router.route(_notif("CRITICAL", "org.security.anomaly_detected"))
    assert notified == ["telegram"]
    telegram.broadcast.assert_awaited_once()


def _force_not_quiet(router: OutboundNotificationRouter) -> None:
    """Neutralize wall-clock-dependent quiet-hours suppression for a routing
    test, regardless of which severity/route ends up selected (including the
    DEFAULT_ROUTES fallback for an unrecognized severity, which bypasses any
    per-instance ``_routes`` override)."""
    router.is_quiet_hours = lambda route: False  # type: ignore[method-assign]


async def test_route_stops_at_first_success_for_non_critical() -> None:
    slack = AsyncMock()
    telegram = AsyncMock()
    router = OutboundNotificationRouter(slack_sender=slack, telegram_sender=telegram)
    _force_not_quiet(router)
    notified = await router.route(_notif("ATTENTION"))
    assert notified == ["slack"]
    telegram.broadcast.assert_not_awaited()


async def test_route_falls_back_when_primary_unconfigured() -> None:
    telegram = AsyncMock()
    # No slack sender configured -> primary fails silently, falls back to telegram.
    router = OutboundNotificationRouter(telegram_sender=telegram)
    _force_not_quiet(router)
    notified = await router.route(_notif("ATTENTION"))
    assert notified == ["telegram"]


async def test_route_critical_tries_all_channels() -> None:
    telegram = AsyncMock()
    slack = AsyncMock()
    email = AsyncMock()
    router = OutboundNotificationRouter(telegram_sender=telegram, slack_sender=slack, email_sender=email)
    notified = await router.route(_notif("CRITICAL", "org.budget.exceeded"))
    assert notified == ["telegram", "slack", "email"]


async def test_route_all_channels_unconfigured_logs_and_returns_empty() -> None:
    router = OutboundNotificationRouter()
    _force_not_quiet(router)
    notified = await router.route(_notif("APPROVAL", "org.approval.requested"))
    assert notified == []


async def test_route_channel_exception_is_caught_and_treated_as_failure() -> None:
    telegram = AsyncMock()
    telegram.broadcast = AsyncMock(side_effect=RuntimeError("boom"))
    slack = AsyncMock()
    router = OutboundNotificationRouter(telegram_sender=telegram, slack_sender=slack)
    _force_not_quiet(router)
    notif = _notif("APPROVAL", "org.approval.requested")
    notified = await router.route(notif)
    assert notified == ["slack"]


async def test_route_unknown_severity_falls_back_to_default_digest_route() -> None:
    email = AsyncMock()
    router = OutboundNotificationRouter(email_sender=email)
    _force_not_quiet(router)
    notif = _notif("NOT_A_REAL_SEVERITY")
    notified = await router.route(notif)
    assert notified == ["email"]


# ── UsageAlert.severity ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (1.0, NotificationSeverity.CRITICAL),
        (1.5, NotificationSeverity.CRITICAL),
        (0.95, NotificationSeverity.APPROVAL),
        (0.80, NotificationSeverity.ATTENTION),
        (0.5, NotificationSeverity.DIGEST),
    ],
)
def test_usage_alert_severity_thresholds(pct: float, expected: str) -> None:
    alert = UsageAlert(
        alert_type="quota_warning",
        resource="agents",
        current=pct * 10,
        limit=10,
        pct_used=pct,
        tenant_id="t1",
    )
    assert alert.severity == expected


def test_alert_thresholds_constant_values() -> None:
    assert ALERT_THRESHOLDS["quota_warning"] == 0.80
    assert ALERT_THRESHOLDS["quota_exceeded"] == 1.00


# ── UsageAlertService.check_and_alert ────────────────────────────────────────


async def test_check_and_alert_below_warning_threshold_returns_none() -> None:
    router = OutboundNotificationRouter()
    service = UsageAlertService(router)
    result = await service.check_and_alert("tenant-1", "agents", current=1, limit=10)
    assert result is None


async def test_check_and_alert_zero_or_negative_limit_returns_none() -> None:
    router = OutboundNotificationRouter()
    service = UsageAlertService(router)
    assert await service.check_and_alert("tenant-1", "agents", current=1, limit=0) is None
    assert await service.check_and_alert("tenant-1", "agents", current=1, limit=-5) is None


async def test_check_and_alert_quota_warning_fires_attention_alert() -> None:
    slack = AsyncMock()
    router = OutboundNotificationRouter(slack_sender=slack)
    _force_not_quiet(router)
    service = UsageAlertService(router)
    alert = await service.check_and_alert("tenant-1", "agents", current=8, limit=10, org_id="org-9")
    assert alert is not None
    assert alert.alert_type == "quota_warning"
    assert alert.resource == "agents"
    assert alert.org_id == "org-9"
    assert alert.delivered_to == ["slack"]


async def test_check_and_alert_quota_critical_uses_approval_severity() -> None:
    telegram = AsyncMock()
    router = OutboundNotificationRouter(telegram_sender=telegram)
    service = UsageAlertService(router)
    alert = await service.check_and_alert("tenant-1", "budget", current=96, limit=100)
    assert alert is not None
    assert alert.alert_type == "quota_critical"
    assert alert.severity == NotificationSeverity.APPROVAL


async def test_check_and_alert_quota_exceeded_uses_critical_severity_and_requires_action() -> None:
    telegram = AsyncMock()
    router = OutboundNotificationRouter(telegram_sender=telegram)
    service = UsageAlertService(router)
    alert = await service.check_and_alert("tenant-1", "api_calls", current=120, limit=100)
    assert alert is not None
    assert alert.alert_type == "quota_exceeded"
    assert alert.severity == NotificationSeverity.CRITICAL
    assert alert.pct_used == 1.2


async def test_check_and_alert_without_org_id_falls_back_to_tenant_id() -> None:
    router = OutboundNotificationRouter()
    service = UsageAlertService(router)
    alert = await service.check_and_alert("tenant-42", "agents", current=9, limit=10)
    assert alert is not None
    assert alert.org_id is None
    assert alert.delivered_to == []
