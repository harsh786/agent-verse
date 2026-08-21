"""SUPPLEMENT F — Failure Management (5-class failure taxonomy).

CLASS 1: TRANSIENT      → retry with exponential backoff
CLASS 2: DEGRADED       → fallback to alternative
CLASS 3: BLOCKED        → escalation required
CLASS 4: FATAL          → recovery / queue
CLASS 5: CATASTROPHIC   → human always required

Includes OrgDLQ (Dead Letter Queue) for unrecoverable events.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)


class FailureClass(int, Enum):
    TRANSIENT = 1  # retry
    DEGRADED = 2  # fallback
    BLOCKED = 3  # escalation
    FATAL = 4  # recovery / queue
    CATASTROPHIC = 5  # human always required


@dataclass
class FailureEvent:
    failure_id: str
    failure_class: FailureClass
    error_message: str
    context: dict[str, Any]
    org_id: str
    tenant_id: str
    mission_id: str | None = None
    agent_id: str | None = None
    retry_count: int = 0
    max_retries: int = 5
    backoff_seconds: list[int] = field(default_factory=lambda: [1, 5, 30, 300, 1800])
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved: bool = False
    resolution: str | None = None


@dataclass
class DLQEntry:
    entry_id: str
    event: FailureEvent
    reason: str
    retry_count: int = 0
    status: str = "pending"  # pending | retrying | dismissed
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_retry_at: datetime | None = None


class FailureClassifier:
    """Classify failures into the 5-class taxonomy."""

    # Transient error patterns
    TRANSIENT_ERRORS = frozenset(
        {
            "timeout",
            "connection_reset",
            "rate_limit",
            "503",
            "502",
            "429",
            "network_error",
            "dns_error",
            "temporary_failure",
        }
    )

    # Degraded error patterns
    DEGRADED_ERRORS = frozenset(
        {
            "quality_below_threshold",
            "model_partial_failure",
            "tool_unreliable",
            "context_overflow",
            "knowledge_empty",
        }
    )

    # Blocked patterns
    BLOCKED_PATTERNS = frozenset(
        {
            "approval_timeout",
            "budget_exceeded",
            "policy_violation",
            "circular_dependency",
            "missing_permission",
        }
    )

    # Fatal patterns
    FATAL_PATTERNS = frozenset(
        {
            "infinite_loop",
            "task_poisoned",
            "all_providers_down",
            "max_retries_exceeded",
            "deadlock_detected",
        }
    )

    # Catastrophic patterns — NEVER auto-recover
    CATASTROPHIC_PATTERNS = frozenset(
        {
            "data_corruption",
            "security_breach",
            "financial_unauthorized",
            "compliance_violation",
            "pii_bulk_operation",
            "prod_infra_destruction",
        }
    )

    def classify(self, error_type: str, context: dict[str, Any]) -> FailureClass:
        err_lower = error_type.lower()

        for pattern in self.CATASTROPHIC_PATTERNS:
            if pattern in err_lower:
                return FailureClass.CATASTROPHIC

        for pattern in self.FATAL_PATTERNS:
            if pattern in err_lower:
                return FailureClass.FATAL

        for pattern in self.BLOCKED_PATTERNS:
            if pattern in err_lower:
                return FailureClass.BLOCKED

        for pattern in self.DEGRADED_ERRORS:
            if pattern in err_lower:
                return FailureClass.DEGRADED

        for pattern in self.TRANSIENT_ERRORS:
            if pattern in err_lower:
                return FailureClass.TRANSIENT

        # Default: treat as transient for first attempt
        return FailureClass.TRANSIENT


class OrgFailureManager:
    """
    SUPPLEMENT F — Org-layer failure management.

    Handles the 5-class taxonomy:
      CLASS 1 (TRANSIENT):    exponential backoff (1s→5s→30s→5min→30min)
      CLASS 2 (DEGRADED):     model/tool fallback cascade
      CLASS 3 (BLOCKED):      escalation + notification
      CLASS 4 (FATAL):        queue + human notification
      CLASS 5 (CATASTROPHIC): stop + human always required
    """

    HARD_LIMITS = frozenset(
        {
            "production_infra_destruction",
            "mass_customer_data_deletion",
            "external_financial_transfer_gt_10k",
            "binding_legal_agreements",
            "press_releases",
            "mass_pii_bulk_operations",
        }
    )

    def __init__(
        self,
        notification_router: Any | None = None,
        dlq: OrgDLQ | None = None,
    ) -> None:
        self._notify = notification_router
        self._dlq = dlq or OrgDLQ()
        self._classifier = FailureClassifier()

    async def handle(self, failure: FailureEvent) -> str:
        """
        Main failure handler. Returns: retry | fallback | escalate | queue | stop
        """
        fc = failure.failure_class
        _log.info(
            "failure_manager.handling",
            failure_id=failure.failure_id,
            failure_class=fc.name,
            org_id=failure.org_id,
        )

        if fc == FailureClass.TRANSIENT:
            return await self._handle_transient(failure)
        if fc == FailureClass.DEGRADED:
            return await self._handle_degraded(failure)
        if fc == FailureClass.BLOCKED:
            return await self._handle_blocked(failure)
        if fc == FailureClass.FATAL:
            return await self._handle_fatal(failure)
        if fc == FailureClass.CATASTROPHIC:
            return await self._handle_catastrophic(failure)
        return "unknown"

    async def _handle_transient(self, failure: FailureEvent) -> str:
        if failure.retry_count < failure.max_retries:
            delay = failure.backoff_seconds[
                min(failure.retry_count, len(failure.backoff_seconds) - 1)
            ]
            _log.info(
                "failure_manager.retry",
                failure_id=failure.failure_id,
                attempt=failure.retry_count + 1,
                delay_s=delay,
            )
            await asyncio.sleep(delay)
            return "retry"
        # Max retries exceeded → escalate
        failure.failure_class = FailureClass.BLOCKED
        return await self._handle_blocked(failure)

    async def _handle_degraded(self, failure: FailureEvent) -> str:
        # Trigger fallback (model/tool alternative)
        _log.info("failure_manager.fallback", failure_id=failure.failure_id)
        return "fallback"

    async def _handle_blocked(self, failure: FailureEvent) -> str:
        # Notify human, pause mission
        if self._notify:
            try:
                from app.gateway.notification_router import (
                    NotificationSeverity,
                    OutboundNotification,
                )

                notif = OutboundNotification(
                    org_id=failure.org_id,
                    event_type="org.mission.blocked",
                    severity=NotificationSeverity.APPROVAL,
                    title=f"Mission Blocked: {failure.error_message[:80]}",
                    body=f"Mission {failure.mission_id} is blocked and needs attention.\nError: {failure.error_message}",
                    requires_action=True,
                )
                await self._notify.route(notif)
            except Exception as exc:
                _log.warning("failure_manager.notify_failed", error=str(exc))
        return "escalate"

    async def _handle_fatal(self, failure: FailureEvent) -> str:
        # Write to DLQ, notify human
        await self._dlq.write(failure.failure_id, failure, "fatal_failure_max_retries")
        if self._notify:
            try:
                from app.gateway.notification_router import (
                    NotificationSeverity,
                    OutboundNotification,
                )

                notif = OutboundNotification(
                    org_id=failure.org_id,
                    event_type="org.anomaly.detected",
                    severity=NotificationSeverity.CRITICAL,
                    title=f"Fatal Failure — {failure.error_message[:60]}",
                    body=f"Mission {failure.mission_id} encountered a fatal error and has been queued for recovery.",
                    requires_action=True,
                )
                await self._notify.route(notif)
            except Exception as exc:
                _log.warning("failure_manager.notify_fatal_failed", error=str(exc))
        return "queue"

    async def _handle_catastrophic(self, failure: FailureEvent) -> str:
        # STOP EVERYTHING — human required
        _log.critical(
            "failure_manager.CATASTROPHIC",
            failure_id=failure.failure_id,
            error=failure.error_message,
            org_id=failure.org_id,
        )
        await self._dlq.write(failure.failure_id, failure, "catastrophic_human_required")
        return "stop"

    def classify(self, error_type: str, context: dict[str, Any]) -> FailureClass:
        return self._classifier.classify(error_type, context)

    def is_hard_limit(self, action: str) -> bool:
        """Check if an action crosses a hard limit that NEVER auto-recovers."""
        return any(limit in action.lower() for limit in self.HARD_LIMITS)


class OrgDLQ:
    """
    Dead Letter Queue for unrecoverable org events.
    In production: backed by Redis sorted set or DB table.
    """

    def __init__(self) -> None:
        self._entries: dict[str, DLQEntry] = {}

    async def write(
        self,
        entry_id: str,
        event: FailureEvent,
        reason: str,
        retry_count: int = 0,
    ) -> DLQEntry:
        import uuid

        entry = DLQEntry(
            entry_id=entry_id or str(uuid.uuid4()),
            event=event,
            reason=reason,
            retry_count=retry_count,
        )
        self._entries[entry.entry_id] = entry
        _log.info("dlq.written", entry_id=entry.entry_id, reason=reason)
        return entry

    async def list(self, org_id: str, status: str = "pending") -> list[DLQEntry]:
        return [
            e
            for e in self._entries.values()
            if e.event.org_id == org_id and (status == "all" or e.status == status)
        ]

    async def retry(self, entry_id: str) -> bool:
        entry = self._entries.get(entry_id)
        if not entry:
            return False
        entry.status = "retrying"
        entry.retry_count += 1
        entry.last_retry_at = datetime.now(UTC)
        _log.info("dlq.retry", entry_id=entry_id, attempt=entry.retry_count)
        return True

    async def dismiss(self, entry_id: str, reason: str) -> None:
        if entry := self._entries.get(entry_id):
            entry.status = "dismissed"
            entry.event.resolution = reason
            _log.info("dlq.dismissed", entry_id=entry_id, reason=reason)
