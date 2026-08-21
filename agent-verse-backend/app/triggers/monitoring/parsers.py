"""Monitoring alert parsers — Grafana, CloudWatch, Sentry, log_pattern."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class AlertPayload:
    alert_name: str = ""
    severity: str = ""
    state: str = ""
    message: str = ""
    source: str = ""
    labels: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def parse_grafana_alert(body: dict) -> AlertPayload:
    """Parse a Grafana unified alerting webhook payload."""
    alerts = body.get("alerts", [body])
    first = alerts[0] if alerts else {}
    return AlertPayload(
        alert_name=first.get("labels", {}).get("alertname", body.get("title", "")),
        severity=first.get("labels", {}).get("severity", ""),
        state=body.get("status", ""),
        message=first.get("annotations", {}).get("description", ""),
        source="grafana",
        labels=first.get("labels", {}),
        raw=body,
    )


def parse_cloudwatch_alarm(body: dict) -> AlertPayload:
    """Parse a CloudWatch SNS notification payload."""
    detail = body.get("detail", body)
    return AlertPayload(
        alert_name=detail.get("alarmName", ""),
        severity=detail.get("configuration", {}).get("description", ""),
        state=detail.get("state", {}).get("value", ""),
        message=detail.get("state", {}).get("reason", ""),
        source="cloudwatch",
        labels={
            "namespace": (
                detail.get("configuration", {})
                .get("metrics", [{}])[0]
                .get("metricStat", {})
                .get("metric", {})
                .get("namespace", "")
                if detail.get("configuration", {}).get("metrics")
                else ""
            ),
        },
        raw=body,
    )


def parse_sentry_webhook(body: dict) -> AlertPayload:
    """Parse a Sentry issue webhook payload."""
    issue = body.get("data", {}).get("issue", {})
    return AlertPayload(
        alert_name=issue.get("title", body.get("action", "")),
        severity=issue.get("level", ""),
        state=body.get("action", ""),
        message=issue.get("culprit", ""),
        source="sentry",
        labels={
            "project": body.get("project_slug", ""),
            "environment": issue.get("tags", [{}])[0].get("value", "") if issue.get("tags") else "",
        },
        raw=body,
    )


def parse_pagerduty_webhook(body: dict) -> AlertPayload:
    """Parse a PagerDuty webhook v3 payload."""
    events = body.get("event", {})
    data = events.get("data", {})
    return AlertPayload(
        alert_name=data.get("title", ""),
        severity=data.get("urgency", ""),
        state=events.get("event_type", ""),
        message=data.get("summary", ""),
        source="pagerduty",
        labels={"service": (data.get("service") or {}).get("name", "")},
        raw=body,
    )


def parse_datadog_webhook(body: dict) -> AlertPayload:
    """Parse a Datadog alert webhook payload."""
    return AlertPayload(
        alert_name=body.get("alert_name", body.get("monitor_name", "")),
        severity=body.get("priority", ""),
        state=body.get("alert_status", ""),
        message=body.get("alert_message", body.get("body", "")),
        source="datadog",
        labels={"tags": body.get("tags", "")},
        raw=body,
    )


class LogPatternMatcher:
    """Match log lines against a regex pattern for LOG_PATTERN triggers."""

    def __init__(self, pattern: str, log_stream: str = "") -> None:
        self.pattern = re.compile(pattern) if pattern else None
        self.log_stream = log_stream

    def matches(self, log_line: str, stream: str = "") -> bool:
        """Return True if the log line matches the pattern (and stream if specified)."""
        if self.log_stream and stream and self.log_stream != stream:
            return False
        if self.pattern is None:
            return True
        return bool(self.pattern.search(log_line))

    def extract(self, log_line: str) -> dict:
        """Extract named groups from the log line."""
        if self.pattern is None:
            return {"line": log_line}
        m = self.pattern.search(log_line)
        if m:
            groups = m.groupdict()
            return {"line": log_line, "matched": True, **groups}
        return {"line": log_line, "matched": False}
