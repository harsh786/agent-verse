"""PART 21 + PART 29 — Org Audit Event Publisher.

PART 21: Org-level audit event types (30 ORG_AUDIT_EVENTS).
PART 29: Event architecture — publishes org events to Redis pub/sub.

Every org operation publishes a typed event:
  org.mission.created, org.team.formed, org.approval.requested, etc.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── PART 21: All 30 org audit event types ─────────────────────────────────────

ORG_AUDIT_EVENTS = frozenset(
    {
        # Mission lifecycle
        "org.mission.created",
        "org.mission.started",
        "org.mission.completed",
        "org.mission.failed",
        "org.mission.blocked",
        "org.mission.paused",
        "org.mission.resumed",
        "org.mission.cancelled",
        # Team
        "org.team.forming",
        "org.team.formed",
        "org.team.disbanded",
        # Agent
        "org.agent.activated",
        "org.agent.idle",
        "org.agent.blocked",
        "org.agent.escalated",
        "org.agent.completed_task",
        "org.agent.failed_task",
        # Approval
        "org.approval.requested",
        "org.approval.granted",
        "org.approval.rejected",
        "org.approval.timeout",
        # Budget
        "org.budget.threshold_80",
        "org.budget.exceeded",
        # Policy + Security
        "org.policy.violation",
        "org.anomaly.detected",
        # Model
        "org.model.fallback",
        "org.model.degraded",
        # Memory + Knowledge
        "org.memory.promoted",
        "org.knowledge.stale",
        # Artifacts + Decisions
        "org.artifact.created",
        "org.artifact.approved",
        "org.decision.recorded",
        "org.learning.promoted",
        # Health
        "org.health.degraded",
        "org.health.recovered",
        "org.digest.ready",
    }
)


def _make_envelope(
    event_type: str,
    payload: dict[str, Any],
    tenant_id: str,
    org_id: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> dict[str, Any]:
    """Build a spec-compliant event envelope (PART 29)."""
    return {
        "tenant_id": tenant_id,
        "org_id": org_id,
        "event_type": event_type,
        "payload": payload,
        "timestamp": datetime.now(UTC).isoformat(),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "causation_id": causation_id,
        "trace_id": str(trace.get_current_span().get_span_context().trace_id),
        "version": "1.0",
    }


class OrgEventPublisher:
    """
    PART 29 — Publishes org events to Redis pub/sub and persists to audit trail.

    Usage:
        publisher = OrgEventPublisher(redis_client, audit_service)
        await publisher.publish(
            "org.mission.created",
            {"mission_id": "...", "title": "...", "risk": "medium"},
            tenant_id="t1", org_id="org1",
        )
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self._redis = redis_client
        self._audit = audit_service

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str,
        org_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        severity: str = "info",
    ) -> str:
        """
        Publish an org event.
        Returns the correlation_id.
        Validates event_type against ORG_AUDIT_EVENTS.
        """
        with _tracer.start_as_current_span("org_event.publish") as span:
            if event_type not in ORG_AUDIT_EVENTS:
                _log.warning("org_event.unknown_type", event_type=event_type)

            corr_id = correlation_id or str(uuid.uuid4())
            envelope = _make_envelope(event_type, payload, tenant_id, org_id, corr_id, causation_id)
            span.set_attribute("event_type", event_type)
            span.set_attribute("org_id", org_id)
            span.set_attribute("correlation_id", corr_id)

            # Publish to Redis pub/sub (PART 29)
            if self._redis:
                channel = f"org_events:{tenant_id}:{org_id}"
                try:
                    await self._redis.publish(channel, json.dumps(envelope))
                except Exception as exc:
                    _log.warning("org_event.redis_publish_failed", error=str(exc))

            # Also write to audit trail (PART 21)
            if self._audit:
                try:
                    await self._audit.log(
                        event_type=event_type,
                        entity_type="org",
                        entity_id=org_id,
                        tenant_id=tenant_id,
                        payload=payload,
                        severity=severity,
                        correlation_id=corr_id,
                    )
                except Exception as exc:
                    _log.warning("org_event.audit_write_failed", error=str(exc))

            _log.info(
                "org_event.published",
                event_type=event_type,
                org_id=org_id,
                correlation_id=corr_id,
            )
            return corr_id

    async def publish_mission_event(
        self,
        lifecycle: str,  # created | started | completed | failed | blocked | paused
        mission_id: str,
        org_id: str,
        tenant_id: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        event_type = f"org.mission.{lifecycle}"
        return await self.publish(
            event_type,
            {"mission_id": mission_id, **(extra or {})},
            tenant_id,
            org_id,
        )

    async def publish_team_event(
        self,
        lifecycle: str,  # forming | formed | disbanded
        team_id: str,
        mission_id: str,
        org_id: str,
        tenant_id: str,
    ) -> str:
        return await self.publish(
            f"org.team.{lifecycle}",
            {"team_id": team_id, "mission_id": mission_id},
            tenant_id,
            org_id,
        )

    async def publish_approval_event(
        self,
        lifecycle: str,  # requested | granted | rejected | timeout
        approval_id: str,
        action: str,
        org_id: str,
        tenant_id: str,
        approver: str | None = None,
    ) -> str:
        return await self.publish(
            f"org.approval.{lifecycle}",
            {"approval_id": approval_id, "action": action, "approver": approver},
            tenant_id,
            org_id,
            severity="warning" if lifecycle in ("rejected", "timeout") else "info",
        )

    async def publish_budget_alert(
        self,
        threshold_pct: float,
        dept_id: str | None,
        org_id: str,
        tenant_id: str,
        spent_usd: float = 0.0,
        budget_usd: float = 0.0,
    ) -> str:
        event_type = "org.budget.exceeded" if threshold_pct >= 1.0 else "org.budget.threshold_80"
        return await self.publish(
            event_type,
            {
                "threshold_pct": threshold_pct,
                "dept_id": dept_id,
                "spent_usd": spent_usd,
                "budget_usd": budget_usd,
            },
            tenant_id,
            org_id,
            severity="critical" if threshold_pct >= 1.0 else "warning",
        )

    async def publish_policy_violation(
        self,
        violation_type: str,
        agent_id: str,
        action: str,
        org_id: str,
        tenant_id: str,
    ) -> str:
        return await self.publish(
            "org.policy.violation",
            {"violation_type": violation_type, "agent_id": agent_id, "action": action},
            tenant_id,
            org_id,
            severity="warning",
        )

    async def publish_anomaly(
        self,
        anomaly_type: str,
        details: str,
        org_id: str,
        tenant_id: str,
    ) -> str:
        return await self.publish(
            "org.anomaly.detected",
            {"anomaly_type": anomaly_type, "details": details},
            tenant_id,
            org_id,
            severity="critical",
        )
