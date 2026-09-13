"""
PART 18 — Org-scoped RBAC enforcement.
PART 21 — Org audit event types and publisher.
PART 29 — Complete event taxonomy publisher.

RBAC:
  6 org roles: org_admin | dept_admin | team_lead | agent | viewer | approver
  Scope enforcement per role
  Org context injected into every request

Audit Events:
  All 30+ spec-defined org event types
  Audit record schema with full context
  Publisher that writes to existing audit trail + Redis pub/sub

Event Taxonomy:
  All events with severity routing
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── PART 18: Org RBAC ─────────────────────────────────────────────────────────


class OrgRole(StrEnum):
    ORG_ADMIN = "org_admin"  # full access to org
    DEPT_ADMIN = "dept_admin"  # full access to specific department
    TEAM_LEAD = "team_lead"  # manages a team
    AGENT = "agent"  # can view org, execute tasks
    VIEWER = "viewer"  # read-only
    APPROVER = "approver"  # can only approve/reject HITL items


# Permissions per role
ORG_ROLE_PERMISSIONS: dict[OrgRole, list[str]] = {
    OrgRole.ORG_ADMIN: [
        "read",
        "write",
        "create_mission",
        "update_org",
        "manage_agents",
        "approve",
        "reject",
        "change_autonomy",
        "view_budget",
        "change_budget",
        "view_memory",
        "write_memory",
        "view_audit",
        "manage_knowledge",
        "admin",
    ],
    OrgRole.DEPT_ADMIN: [
        "read",
        "write",
        "create_mission",
        "manage_agents",
        "approve",
        "reject",
        "view_budget",
        "view_memory",
        "write_memory",
    ],
    OrgRole.TEAM_LEAD: [
        "read",
        "write",
        "create_mission",
        "manage_team",
        "approve",
        "view_memory",
    ],
    OrgRole.AGENT: ["read", "create_mission", "view_memory"],
    OrgRole.VIEWER: ["read"],
    OrgRole.APPROVER: ["read", "approve", "reject"],
}


def has_permission(role: OrgRole | str, permission: str) -> bool:
    """Check if an org role grants a specific permission."""
    if isinstance(role, str):
        try:
            role = OrgRole(role)
        except ValueError:
            return False
    return permission in ORG_ROLE_PERMISSIONS.get(role, [])


def assert_permission(role: OrgRole | str, permission: str) -> None:
    """Raise PermissionError if role doesn't have permission."""
    if not has_permission(role, permission):
        raise PermissionError(f"Role '{role}' does not have permission '{permission}'")


# ── PART 21: Org Audit Event Types ────────────────────────────────────────────

# All 30+ org audit event types from spec PART 21
ORG_AUDIT_EVENTS = [
    "org.created",
    "org.updated",
    "org.deleted",
    "org.mission.created",
    "org.mission.started",
    "org.mission.completed",
    "org.mission.failed",
    "org.mission.paused",
    "org.mission.cancelled",
    "org.mission.blocked",
    "org.mission.active",
    "org.mission.progress",
    "org.team.forming",
    "org.team.formed",
    "org.team.disbanded",
    "org.task.created",
    "org.task.decomposed",
    "org.task.assigned",
    "org.task.handoff",
    "org.agent.assigned",
    "org.agent.activated",
    "org.agent.working",
    "org.agent.idle",
    "org.agent.completed_task",
    "org.agent.failed_task",
    "org.agent.escalated",
    "org.agent.blocked",
    "org.approval.requested",
    "org.approval.granted",
    "org.approval.rejected",
    "org.approval.timeout",
    "org.budget.threshold_80",
    "org.budget.threshold_95",
    "org.budget.exceeded",
    "org.policy.violation",
    "org.cross_dept.message_sent",
    "org.collaboration.message",
    "org.memory.promoted",
    "org.knowledge.updated",
    "org.knowledge.stale",
    "org.artifact.created",
    "org.artifact.approved",
    "org.artifact.rejected",
    "org.decision.recorded",
    "org.decision.outcome_measured",
    "org.reputation.updated",
    "org.model.fallback",
    "org.model.degraded",
    "org.security.anomaly_detected",
    "org.learning.promoted",
    "org.health.degraded",
    "org.health.recovered",
    "org.digest.ready",
    "org.emergency_stop.triggered",
    "org.emergency_stop.resumed",
]

# Map each event to its notification severity
from app.gateway.notification_router import EVENT_SEVERITY_MAP, NotificationSeverity  # noqa: E402


@dataclass
class OrgAuditRecord:
    """
    Full audit record per spec PART 21.
    Written to audit trail on every org event.
    """

    id: str
    tenant_id: str
    org_id: str
    event_type: str
    mission_id: str | None = None
    department_id: str | None = None
    team_id: str | None = None
    agent_id: str | None = None
    role_id: str | None = None
    model_id: str | None = None
    tool_id: str | None = None
    action: str = ""
    outcome: str = ""
    policy_reference: str | None = None
    risk_level: str = "low"
    human_approver: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── PART 29: Event Publisher ──────────────────────────────────────────────────


class OrgEventPublisher:
    """
    Publishes org events to:
      1. Existing audit trail (async DB write)
      2. Redis pub/sub (for real-time SSE to frontend)
      3. Outbound notification router (for channel delivery)
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        audit_service: Any | None = None,
        notification_router: Any | None = None,
    ) -> None:
        self._redis = redis_client
        self._audit = audit_service
        self._notifier = notification_router

    async def publish(
        self,
        event_type: str,
        org_id: str,
        tenant_id: str,
        payload: dict[str, Any] | None = None,
        audit_record: OrgAuditRecord | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """
        Publish an org event to all downstream consumers.
        All 30+ event types are handled by this single method.
        """
        if event_type not in ORG_AUDIT_EVENTS:
            _log.warning("org_event.unknown_type", event_type=event_type, org_id=org_id)

        with _tracer.start_as_current_span("org_event.publish") as span:
            span.set_attribute("event_type", event_type)
            span.set_attribute("org_id", org_id)

            envelope = {
                "tenant_id": tenant_id,
                "org_id": org_id,
                "event_type": event_type,
                "payload": payload or {},
                "timestamp": datetime.now(UTC).isoformat(),
                "correlation_id": correlation_id,
                "version": "1.0",
            }

            # 1. Redis pub/sub (powers real-time SSE)
            if self._redis:
                try:
                    channel = f"org:{org_id}:events"
                    await self._redis.publish(channel, json.dumps(envelope))
                except Exception as exc:
                    _log.warning("org_event.redis_fail", event=event_type, error=str(exc))

            # 2. Audit trail
            if audit_record:
                try:
                    await self._write_audit(audit_record)
                except Exception as exc:
                    _log.warning("org_event.audit_fail", event=event_type, error=str(exc))

            # 3. Outbound notification routing
            if self._notifier:
                severity = EVENT_SEVERITY_MAP.get(event_type, NotificationSeverity.SILENT)
                if severity != NotificationSeverity.SILENT:
                    try:
                        from app.gateway.notification_router import OutboundNotification

                        notif = OutboundNotification(
                            org_id=org_id,
                            event_type=event_type,
                            severity=severity,
                            title=self._event_title(event_type),
                            body=self._event_body(event_type, payload or {}),
                            metadata=envelope,
                        )
                        await self._notifier.route(notif)
                    except Exception as exc:
                        _log.warning("org_event.notify_fail", event=event_type, error=str(exc))

            _log.debug("org_event.published", event_type=event_type, org_id=org_id)

    async def _write_audit(self, record: OrgAuditRecord) -> None:
        """Write to audit trail (delegates to existing audit service)."""
        if self._audit and hasattr(self._audit, "log"):
            await self._audit.log(record.to_dict())

    @staticmethod
    def _event_title(event_type: str) -> str:
        parts = event_type.split(".")
        return (
            " ".join(p.replace("_", " ").title() for p in parts[1:])
            if len(parts) > 1
            else event_type
        )

    @staticmethod
    def _event_body(event_type: str, payload: dict) -> str:
        base = OrgEventPublisher._event_title(event_type)
        if mission_id := payload.get("mission_id"):
            return f"{base} (mission: {mission_id[:8]})"
        if agent_id := payload.get("agent_id"):
            return f"{base} (agent: {agent_id[:8]})"
        return base


# Global singleton (wired in main.py lifespan)
_publisher: OrgEventPublisher | None = None


def get_org_event_publisher() -> OrgEventPublisher:
    global _publisher
    if _publisher is None:
        _publisher = OrgEventPublisher()
    return _publisher


def configure_org_event_publisher(
    redis_client: Any | None = None,
    audit_service: Any | None = None,
    notification_router: Any | None = None,
) -> None:
    global _publisher
    _publisher = OrgEventPublisher(redis_client, audit_service, notification_router)


async def publish_org_event(
    event_type: str,
    org_id: str,
    tenant_id: str,
    payload: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Convenience shortcut for publishing org events."""
    await get_org_event_publisher().publish(
        event_type=event_type,
        org_id=org_id,
        tenant_id=tenant_id,
        payload=payload,
        **kwargs,
    )
