"""Outbound notification router — QA6 of spec.

Routes org events outbound to the right channel(s).

Notification severities:
  SILENT    → no notification
  DIGEST    → daily email
  ATTENTION → Slack (primary) → Telegram (fallback)
  APPROVAL  → Telegram (primary) → Slack → Email (fallbacks)
  CRITICAL  → Telegram (primary) → Slack → Email → SMS (all fallbacks)

Quiet hours: configurable per org (default 22:00–07:00 local)
Critical notifications bypass quiet hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as dt_time
from enum import StrEnum
from typing import Any

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class NotificationSeverity(StrEnum):
    SILENT = "SILENT"
    DIGEST = "DIGEST"
    ATTENTION = "ATTENTION"
    APPROVAL = "APPROVAL"
    CRITICAL = "CRITICAL"


@dataclass
class NotificationRoute:
    severity: str
    primary_channel: str  # telegram | slack | teams | email | push | none
    fallback_channels: list[str]
    quiet_hours_start: str = "22:00"  # HH:MM
    quiet_hours_end: str = "07:00"
    override_quiet_for_critical: bool = True


# ── Default routing (user-configurable per org) ───────────────────────────────

DEFAULT_ROUTES: dict[str, NotificationRoute] = {
    NotificationSeverity.SILENT: NotificationRoute("SILENT", "none", []),
    NotificationSeverity.DIGEST: NotificationRoute("DIGEST", "email", []),
    NotificationSeverity.ATTENTION: NotificationRoute("ATTENTION", "slack", ["telegram"]),
    NotificationSeverity.APPROVAL: NotificationRoute("APPROVAL", "telegram", ["slack", "email"]),
    NotificationSeverity.CRITICAL: NotificationRoute(
        "CRITICAL", "telegram", ["slack", "email", "sms"]
    ),
}


# ── Event-to-severity mapping ─────────────────────────────────────────────────

EVENT_SEVERITY_MAP: dict[str, str] = {
    # Critical
    "org.security.anomaly_detected": NotificationSeverity.CRITICAL,
    "org.budget.exceeded": NotificationSeverity.CRITICAL,
    "org.mission.failed": NotificationSeverity.CRITICAL,
    # Approval
    "org.approval.requested": NotificationSeverity.APPROVAL,
    "org.budget.threshold_80": NotificationSeverity.APPROVAL,
    # Attention
    "org.mission.blocked": NotificationSeverity.ATTENTION,
    "org.agent.escalated": NotificationSeverity.ATTENTION,
    "org.model.fallback": NotificationSeverity.ATTENTION,
    # Digest
    "org.mission.completed": NotificationSeverity.DIGEST,
    "org.mission.started": NotificationSeverity.DIGEST,
    "org.learning.promoted": NotificationSeverity.DIGEST,
    "org.digest.ready": NotificationSeverity.DIGEST,
    # Silent
    "org.agent.activated": NotificationSeverity.SILENT,
    "org.agent.idle": NotificationSeverity.SILENT,
}


@dataclass
class OutboundNotification:
    org_id: str
    event_type: str
    severity: str
    title: str
    body: str
    action_url: str | None = None
    requires_action: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class OutboundNotificationRouter:
    """
    Routes org events outbound to the right channel(s).
    Respects quiet hours and severity levels.
    """

    def __init__(
        self,
        routes: dict[str, NotificationRoute] | None = None,
        # Injected channel senders (set by dependency injection at startup)
        telegram_sender: Any | None = None,
        slack_sender: Any | None = None,
        email_sender: Any | None = None,
    ) -> None:
        self._routes = routes or DEFAULT_ROUTES
        self._telegram = telegram_sender
        self._slack = slack_sender
        self._email = email_sender

    def severity_for_event(self, event_type: str) -> str:
        return EVENT_SEVERITY_MAP.get(event_type, NotificationSeverity.DIGEST)

    def is_quiet_hours(self, route: NotificationRoute) -> bool:
        """Check if current time falls within quiet hours."""
        now = datetime.now(UTC).time()
        start = dt_time.fromisoformat(route.quiet_hours_start)
        end = dt_time.fromisoformat(route.quiet_hours_end)
        if start > end:
            # Wraps midnight (e.g. 22:00 – 07:00)
            return now >= start or now <= end
        return start <= now <= end

    async def route(self, notification: OutboundNotification) -> list[str]:
        """
        Route the notification to the appropriate channel(s).
        Returns list of channels actually notified.
        """
        with _tracer.start_as_current_span("notification.route") as span:
            span.set_attribute("org_id", notification.org_id)
            span.set_attribute("severity", notification.severity)
            span.set_attribute("event_type", notification.event_type)

            route = self._routes.get(
                notification.severity, DEFAULT_ROUTES[NotificationSeverity.DIGEST]
            )

            if route.primary_channel == "none":
                return []

            in_quiet = self.is_quiet_hours(route)
            is_critical = notification.severity == NotificationSeverity.CRITICAL

            if in_quiet and not (is_critical and route.override_quiet_for_critical):
                _log.debug("notification.suppressed_quiet_hours", event=notification.event_type)
                return []

            notified = []
            # Try primary, then fallbacks
            channels = [route.primary_channel, *list(route.fallback_channels)]
            for channel in channels:
                sent = await self._send_to_channel(channel, notification)
                if sent:
                    notified.append(channel)
                    if not is_critical:
                        break  # Stop at first success for non-critical

            if not notified:
                _log.warning("notification.all_channels_failed", event=notification.event_type)

            return notified

    async def _send_to_channel(self, channel: str, notification: OutboundNotification) -> bool:
        """Dispatch to channel sender. Returns True if sent."""
        try:
            if channel == "telegram" and self._telegram:
                await self._telegram.broadcast(notification.body, notification.org_id)
                return True
            if channel == "slack" and self._slack:
                await self._slack.broadcast(notification.body, notification.org_id)
                return True
            if channel == "email" and self._email:
                await self._email.send(
                    subject=notification.title,
                    body=notification.body,
                    org_id=notification.org_id,
                )
                return True
            # Channel not configured
            return False
        except Exception as exc:
            _log.warning("notification.channel_failed", channel=channel, error=str(exc))
            return False


# ── Usage alert system (QA2) ─────────────────────────────────────────────────


@dataclass
class UsageAlert:
    """Fired when tenant approaches or exceeds a quota."""

    alert_type: str  # quota_warning | quota_exceeded | budget_warning
    resource: str  # agents | missions | monthly_budget | api_calls
    current: float
    limit: float
    pct_used: float  # 0-1
    tenant_id: str
    org_id: str | None = None
    delivered_to: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if self.pct_used >= 1.0:
            return NotificationSeverity.CRITICAL
        if self.pct_used >= 0.95:
            return NotificationSeverity.APPROVAL
        if self.pct_used >= 0.80:
            return NotificationSeverity.ATTENTION
        return NotificationSeverity.DIGEST


ALERT_THRESHOLDS = {
    "quota_warning": 0.80,  # 80% → warn
    "quota_critical": 0.95,  # 95% → urgent
    "quota_exceeded": 1.00,  # 100% → block + alert
    "budget_warning": 0.70,  # 70% → warn
    "budget_critical": 0.90,  # 90% → start pausing low-priority
    "budget_exceeded": 1.00,  # 100% → pause all non-critical
}


class UsageAlertService:
    """Checks tenant quotas and fires alerts when thresholds are crossed."""

    def __init__(self, router: OutboundNotificationRouter) -> None:
        self._router = router

    async def check_and_alert(
        self,
        tenant_id: str,
        resource: str,
        current: float,
        limit: float,
        org_id: str | None = None,
    ) -> UsageAlert | None:
        """Check a resource usage and fire alert if threshold crossed."""
        if limit <= 0:
            return None

        pct = current / limit
        if pct < ALERT_THRESHOLDS["quota_warning"]:
            return None

        alert_type = (
            "quota_exceeded" if pct >= 1.0 else "quota_critical" if pct >= 0.95 else "quota_warning"
        )
        alert = UsageAlert(
            alert_type=alert_type,
            resource=resource,
            current=current,
            limit=limit,
            pct_used=round(pct, 3),
            tenant_id=tenant_id,
            org_id=org_id,
        )

        notif = OutboundNotification(
            org_id=org_id or tenant_id,
            event_type=f"org.{alert_type}",
            severity=alert.severity,
            title=f"Usage Alert: {resource} at {pct * 100:.0f}%",
            body=(
                f"{'⚠️' if pct < 1.0 else '🚨'} {resource.title()} usage: "
                f"{current:.0f}/{limit:.0f} ({pct * 100:.0f}%)\n"
                f"{'Upgrade plan or increase limit to avoid service interruption.' if pct >= 0.95 else 'Consider upgrading.'}"  # noqa: E501
            ),
            requires_action=pct >= 0.95,
        )

        notified = await self._router.route(notif)
        alert.delivered_to = notified
        _log.info(
            "usage_alert.fired",
            alert_type=alert_type,
            resource=resource,
            pct=pct,
            notified=notified,
        )
        return alert
