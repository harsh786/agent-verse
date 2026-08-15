"""Tests for monitoring parsers — Grafana, CloudWatch, Sentry, PagerDuty, LogPattern."""
from __future__ import annotations

from app.triggers.monitoring.parsers import (
    parse_grafana_alert,
    parse_cloudwatch_alarm,
    parse_sentry_webhook,
    parse_pagerduty_webhook,
    parse_datadog_webhook,
    LogPatternMatcher,
)


# ── Grafana ───────────────────────────────────────────────────────────────────

def test_grafana_parse_alert():
    body = {
        "status": "firing",
        "title": "High CPU",
        "alerts": [{
            "labels": {"alertname": "CPUHigh", "severity": "critical"},
            "annotations": {"description": "CPU above 90%"},
        }],
    }
    p = parse_grafana_alert(body)
    assert p.alert_name == "CPUHigh"
    assert p.severity == "critical"
    assert p.state == "firing"
    assert p.source == "grafana"
    assert "CPU above 90%" in p.message


def test_grafana_parse_empty():
    p = parse_grafana_alert({})
    assert p.source == "grafana"
    assert p.alert_name == ""


# ── CloudWatch ────────────────────────────────────────────────────────────────

def test_cloudwatch_parse_alarm():
    body = {
        "detail": {
            "alarmName": "HighErrorRate",
            "state": {"value": "ALARM", "reason": "Threshold crossed"},
        }
    }
    p = parse_cloudwatch_alarm(body)
    assert p.alert_name == "HighErrorRate"
    assert p.state == "ALARM"
    assert p.source == "cloudwatch"


def test_cloudwatch_parse_empty():
    p = parse_cloudwatch_alarm({})
    assert p.source == "cloudwatch"


# ── Sentry ────────────────────────────────────────────────────────────────────

def test_sentry_parse_webhook():
    body = {
        "action": "created",
        "project_slug": "my-app",
        "data": {
            "issue": {
                "title": "NullPointerException",
                "level": "error",
                "culprit": "app.views.main",
            }
        },
    }
    p = parse_sentry_webhook(body)
    assert p.alert_name == "NullPointerException"
    assert p.severity == "error"
    assert p.state == "created"
    assert p.source == "sentry"
    assert p.labels["project"] == "my-app"


# ── PagerDuty ─────────────────────────────────────────────────────────────────

def test_pagerduty_parse_webhook():
    body = {
        "event": {
            "event_type": "incident.trigger",
            "data": {
                "title": "API Gateway Down",
                "urgency": "high",
                "summary": "P1 incident",
                "service": {"name": "API Gateway"},
                "id": "Q1ABC",
            },
        }
    }
    p = parse_pagerduty_webhook(body)
    assert p.alert_name == "API Gateway Down"
    assert p.severity == "high"
    assert p.state == "incident.trigger"
    assert p.source == "pagerduty"
    assert p.labels["service"] == "API Gateway"


# ── Datadog ───────────────────────────────────────────────────────────────────

def test_datadog_parse_webhook():
    body = {
        "alert_name": "p99_latency",
        "priority": "P1",
        "alert_status": "triggered",
        "alert_message": "P99 latency above 2s",
        "tags": "env:prod,service:api",
    }
    p = parse_datadog_webhook(body)
    assert p.alert_name == "p99_latency"
    assert p.severity == "P1"
    assert p.state == "triggered"
    assert p.source == "datadog"


# ── LogPatternMatcher ─────────────────────────────────────────────────────────

def test_log_pattern_match():
    m = LogPatternMatcher(r"ERROR.*database", "app.log")
    assert m.matches("ERROR: cannot connect to database", "app.log") is True


def test_log_pattern_no_match():
    m = LogPatternMatcher(r"CRITICAL")
    assert m.matches("INFO: all good", "") is False


def test_log_pattern_stream_filter():
    m = LogPatternMatcher(r"ERROR", "error.log")
    # Different stream — no match
    assert m.matches("ERROR: something", "access.log") is False
    # Correct stream
    assert m.matches("ERROR: something", "error.log") is True


def test_log_pattern_extract_groups():
    m = LogPatternMatcher(r"status=(?P<code>\d{3})")
    result = m.extract("GET / HTTP/1.1 status=404")
    assert result["matched"] is True
    assert result["code"] == "404"


def test_log_pattern_no_match_extract():
    m = LogPatternMatcher(r"ERROR")
    result = m.extract("INFO: all good")
    assert result["matched"] is False


def test_log_pattern_empty():
    m = LogPatternMatcher("")
    assert m.matches("anything", "") is True
