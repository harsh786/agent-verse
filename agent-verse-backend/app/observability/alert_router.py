"""Alert Router — threshold-based metric alerting with webhook delivery.

Supports Slack-compatible and generic JSON webhook payloads.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AlertRule:
    metric: str
    threshold: float
    window_seconds: int
    severity: str  # "critical" | "warning" | "info"
    webhook_url: str
    comparison: str = "gt"  # "gt" (>), "lt" (<), "eq" (==)
    name: str = ""
    tenant_id: str = ""  # empty = global rule


@dataclass
class FiredAlert:
    rule_name: str
    metric: str
    value: float
    threshold: float
    severity: str
    tenant_id: str
    fired_at: float = field(default_factory=time.time)
    webhook_url: str = ""


class AlertRouter:
    """Evaluate metric values against registered rules and fire alerts.

    Thread-safe for async use. Does NOT require a database — rules are
    held in-memory and can be re-seeded on startup from config.
    """

    def __init__(self) -> None:
        self._rules: list[AlertRule] = []
        # Simple in-memory cooldown: {rule_name: last_fired_ts}
        self._cooldowns: dict[str, float] = {}
        self._cooldown_seconds: float = 300.0  # 5-minute alert cooldown

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def register_rule(self, rule: AlertRule) -> None:
        """Add or replace a rule with the same name."""
        self._rules = [r for r in self._rules if r.name != rule.name]
        self._rules.append(rule)

    def list_rules(self) -> list[AlertRule]:
        return list(self._rules)

    def remove_rule(self, name: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        metric_name: str,
        value: float,
        tenant_id: str = "",
    ) -> list[FiredAlert]:
        """Check *value* against all matching rules and fire alerts.

        Returns the list of alerts that were fired (may be empty).
        """
        fired: list[FiredAlert] = []
        now = time.time()
        for rule in self._rules:
            if rule.metric != metric_name:
                continue
            if rule.tenant_id and rule.tenant_id != tenant_id:
                continue
            # Cooldown check
            last = self._cooldowns.get(rule.name, 0)
            if now - last < self._cooldown_seconds:
                continue
            # Threshold check
            if self._matches(value, rule.comparison, rule.threshold):
                alert = FiredAlert(
                    rule_name=rule.name,
                    metric=metric_name,
                    value=value,
                    threshold=rule.threshold,
                    severity=rule.severity,
                    tenant_id=tenant_id,
                    fired_at=now,
                    webhook_url=rule.webhook_url,
                )
                self._cooldowns[rule.name] = now
                fired.append(alert)
                if rule.webhook_url:
                    try:
                        await self.send_alert(alert, rule.webhook_url)
                    except Exception:
                        pass  # alert routing must not crash the calling path
        return fired

    async def send_alert(self, alert: FiredAlert, webhook_url: str) -> None:
        """POST the alert as a JSON payload to *webhook_url*.

        The payload is compatible with Slack's incoming webhook format.
        """
        import httpx

        payload = self._build_payload(alert)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _matches(self, value: float, comparison: str, threshold: float) -> bool:
        if comparison == "gt":
            return value > threshold
        if comparison == "lt":
            return value < threshold
        if comparison == "eq":
            return abs(value - threshold) < 1e-9
        if comparison == "gte":
            return value >= threshold
        if comparison == "lte":
            return value <= threshold
        return False

    def _build_payload(self, alert: FiredAlert) -> dict[str, Any]:
        emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(alert.severity, "⚪")
        text = (
            f"{emoji} *AgentVerse Alert* — {alert.severity.upper()}\n"
            f"Metric: `{alert.metric}` = {alert.value:.4f} "
            f"(threshold {alert.threshold:.4f})\n"
            f"Tenant: `{alert.tenant_id or 'global'}`"
        )
        return {"text": text, "alert": {
            "rule": alert.rule_name,
            "metric": alert.metric,
            "value": alert.value,
            "threshold": alert.threshold,
            "severity": alert.severity,
            "tenant_id": alert.tenant_id,
        }}
