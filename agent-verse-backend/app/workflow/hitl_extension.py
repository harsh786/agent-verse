"""HITLWorkflowGateway — extends HITLGateway with workflow-engine HITL features.

Features (20):
  1. Rich context: 7 display types (image, json, table, diff, chart, number, list)
  2. Custom action buttons with metadata
  3. Custom form schema (reviewer fills structured input on decision)
  4. Escalation chain: auto-escalate after timeout → to_role
  5. Delegation: reviewer hands off to a colleague
  6. Bulk approval: decide multiple requests at once
  7. Magic link JWT: single-use Redis jti guard for email/Slack approval
  8. 4 assignment strategies: round_robin, least_busy, skill_based, specific_user
  9. Priority levels: critical / high / medium / low
 10. Deadline tracking + SLA countdown (deadline = created_at + the step timeout)
 11. timeout_action: auto_approve | auto_reject | escalate | pause — applied once
     per request by the SLA sweep (``check_and_escalate_overdue``)
 12. Discussion thread: comments between reviewers
 13. Audit trail: reviewed_by + reviewed_at stored per decision
 14. Notification hooks: email, Slack, PagerDuty
 15. Mobile-friendly magic link (single-tap approve)
 16. Org-policy integration: route based on PolicyEngine
 17. Idempotency: duplicate decide requests are no-ops
 18. Resume callback: notifies WorkflowRunner after decision
 19. Stats: avg resolution time, pending count by queue
 20. PWA push notification bridge (Phase 5 hook)
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.governance.hitl import HITLGateway
from app.observability.logging import get_logger

_log = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Config models
# ──────────────────────────────────────────────────────────────────────────────

AssignmentStrategy = Literal["round_robin", "least_busy", "skill_based", "specific_user"]
Priority = Literal["critical", "high", "medium", "low"]
TimeoutAction = Literal["auto_approve", "auto_reject", "escalate", "pause"]


@dataclass
class WorkflowHITLRequest:
    """Full HITL request payload sent to the approval inbox."""

    # Identity
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str = ""
    workflow_id: str = ""
    tenant_id: str = ""
    step_id: str = ""
    step_name: str = ""
    workflow_name: str = ""

    # Assignment
    assignment_strategy: AssignmentStrategy = "round_robin"
    assigned_to: str | None = None  # user_id when strategy=specific_user
    assigned_role: str | None = None
    priority: Priority = "medium"

    # Rich context items
    context: list[dict[str, Any]] = field(default_factory=list)

    # Action buttons
    actions: list[dict[str, Any]] = field(default_factory=list)

    # Custom form schema (JSON Schema) — reviewer fills on decide
    custom_form_schema: dict[str, Any] | None = None

    # SLA
    deadline_at: str | None = None
    timeout_action: TimeoutAction = "escalate"

    # Escalation chain
    escalation_to_role: str | None = None
    escalation_after_hours: float = 48.0
    # Set by every escalation (manual or SLA); ``escalation_level`` counts them.
    escalated_at: str | None = None
    escalation_level: int = 0

    # SLA timeout: when the sweep applied ``timeout_action`` (set once — the
    # exactly-once marker, mirrored to ``workflow_approvals.timeout_handled_at``)
    # and what it did: escalated / auto_rejected / auto_approved / paused /
    # escalation_disallowed / expired.
    timed_out_at: str | None = None
    timeout_outcome: str | None = None

    # Allow features
    allow_delegate: bool = True
    allow_escalate: bool = True

    # Discussion
    discussion: list[dict[str, Any]] = field(default_factory=list)

    # Decision
    status: str = "pending"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    action_taken: str | None = None
    form_data: dict[str, Any] | None = None
    note: str = ""

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Magic link support
    magic_link_token: str | None = None
    magic_link_expires_at: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Main gateway class
# ──────────────────────────────────────────────────────────────────────────────


# The system actor the SLA sweep acts as (reviewed_by / audit approver).
SLA_TIMEOUT_ACTOR = "sla-timeout"
# Escalations recorded before the sweep marked ``timed_out_at`` used this actor.
_LEGACY_SLA_ACTOR = "system:sla"
_TIMEOUT_ACTIONS = frozenset({"auto_approve", "auto_reject", "escalate", "pause"})

# claim_timeout results (durable store and in-memory path alike).
CLAIM_APPLIED = "applied"
CLAIM_NOT_PENDING = "not_pending"  # decided / already timed out meanwhile
CLAIM_RUN_ENDED = "run_ended"  # the run is gone or terminal: approval expired
CLAIM_RUN_NOT_WAITING = "run_not_waiting"  # pause before the run suspended: retry


@dataclass(frozen=True)
class TimeoutPlan:
    """What the SLA sweep does to one overdue approval (see ``_timeout_plan``).

    Applied atomically by ``PostgresWorkflowApprovalStore.claim_timeout``: the
    approval changes only while it is still ``pending`` and not yet timed out
    (compare-and-set), together with the run (pause / run_metadata) and the
    audit row, in one transaction.
    """

    outcome: str
    patch: dict[str, Any]
    entry: dict[str, Any]
    status: str | None = None  # a decision (approved / rejected); else stays pending
    assignment: dict[str, Any] | None = None
    bump_escalation: bool = False
    pause_run: bool = False
    audit_note: str = ""

    @property
    def audit_event(self) -> str:
        return f"timeout_{self.outcome}"


class ApprovalNotPendingError(ValueError):
    """The approval was decided (or withdrawn) meanwhile (HTTP 409)."""


class ApprovalPersistenceError(RuntimeError):
    """The durable approval store could not record a change (HTTP 503).

    Never swallowed: an approval that exists only in one process's memory is
    invisible to every reviewer, so its run would wait forever (WF-40)."""


class ApprovalAlreadyDecidedError(Exception):
    """Someone else already decided this approval (HTTP 409)."""

    def __init__(self, req: WorkflowHITLRequest, message: str | None = None) -> None:
        self.request = req
        if message is None:
            if req.status == "cancelled":
                message = "This approval was withdrawn: its workflow run was cancelled"
            elif req.status == "expired":
                message = "This approval expired: its workflow run already ended"
            else:
                message = (
                    f"Already decided by {req.reviewed_by or 'someone else'} "
                    f"({req.action_taken or req.status})"
                )
        super().__init__(message)


@dataclass(frozen=True)
class ReviewerAuthorization:
    """Whether a caller may act (decide / delegate / escalate) on an approval.

    ``override`` is True when only the caller's ``admin`` role allowed it — the
    approval was assigned to someone else — so the caller must audit it.
    """

    allowed: bool
    override: bool = False
    reason: str = ""


def authorize_reviewer(
    req: WorkflowHITLRequest, principal: str, roles: frozenset[str]
) -> ReviewerAuthorization:
    """Decide whether ``principal`` (with expanded ``roles``) may act on ``req``.

    * assigned to a user → only that user;
    * assigned to a role (no user) → members of that role;
    * unassigned → any ``approver``;
    * ``admin`` may always act, flagged as an override when not eligible.

    Old bug: nothing was checked, so any user or API key of the tenant could
    approve or reject an approval assigned to someone else.
    """
    if req.assigned_to:
        eligible = bool(principal) and principal == req.assigned_to
        why = f"assigned to {req.assigned_to!r}"
    elif req.assigned_role:
        eligible = req.assigned_role in roles
        why = f"assigned to role {req.assigned_role!r}"
    else:
        eligible = "approver" in roles
        why = "unassigned approvals need the 'approver' role"
    if eligible:
        return ReviewerAuthorization(True)
    if "admin" in roles:
        return ReviewerAuthorization(True, override=True, reason=why)
    return ReviewerAuthorization(False, reason=why)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def effective_deadline(req: Any) -> datetime | None:
    """When ``req``'s SLA runs out: its ``deadline_at`` (created_at + the step's
    timeout), else — for an approval created without one — ``created_at`` +
    ``escalation_after_hours``; None when neither is known."""
    deadline = _parse_ts(getattr(req, "deadline_at", None))
    if deadline is not None:
        return deadline
    created = _parse_ts(getattr(req, "created_at", None))
    try:
        hours = float(getattr(req, "escalation_after_hours", 0) or 0)
    except (TypeError, ValueError):
        hours = 0.0
    if created is None or hours <= 0:
        return None
    return created + timedelta(hours=hours)


def _is_overdue(req: Any, now: datetime) -> bool:
    deadline = effective_deadline(req)
    return deadline is not None and now >= deadline


def _timeout_handled(req: Any) -> bool:
    """The SLA timeout was already applied to ``req`` (exactly once)."""
    if getattr(req, "timed_out_at", None):
        return True
    return any(
        isinstance(d, dict) and d.get("type") == "escalation" and d.get("by") == _LEGACY_SLA_ACTOR
        for d in (getattr(req, "discussion", None) or [])
    )


def decision_status(action: str) -> str:
    """The approval status a decision leaves: ``approved`` / ``rejected`` for
    the approve / reject families (the DSL and UI ids are ``approve`` /
    ``reject``, which used to fall through to the generic ``decided``), else
    ``decided`` for a custom action id."""
    from app.workflow.steps.hitl_step import APPROVE_ACTIONS, REJECT_ACTIONS

    norm = str(action or "").strip().lower()
    if norm in APPROVE_ACTIONS:
        return "approved"
    if norm in REJECT_ACTIONS:
        return "rejected"
    return "decided"


class HITLWorkflowGateway:
    """Workflow-specific HITL gateway — wraps the base HITLGateway."""

    MAGIC_LINK_TTL_SECONDS = 86_400  # 24h

    def __init__(
        self,
        base_gateway: HITLGateway | None = None,
        redis_client: Any | None = None,
        notification_service: Any | None = None,
        resume_callback: Any | None = None,
        approval_store: Any | None = None,
        event_redis: Any | None = None,
    ) -> None:
        self._base = base_gateway
        self._redis = redis_client
        # Redis the decisions are published to as hitl.approved / hitl.rejected
        # for HITL triggers (B7-3; bound by the API lifespan).
        self._event_redis = event_redis
        self._notify = notification_service
        self._resume_callback = resume_callback
        # Durable, cross-process store (Postgres, RLS). When set, a pending
        # approval created by an out-of-process Celery worker is visible to the
        # API's /approvals endpoints and vice versa (gap #2: cross-process HITL).
        self._approval_store = approval_store
        # In-memory fallback (dev/tests, and process-local mirror).
        self._store: dict[str, WorkflowHITLRequest] = {}
        # Serialises decide() check-and-set when there is no durable store.
        self._decide_lock = asyncio.Lock()

    def set_event_redis(self, redis: Any) -> None:
        self._event_redis = redis

    async def _publish_trigger_event(self, req: WorkflowHITLRequest) -> None:
        """Publish an approve / reject decision for HITLTriggerConsumer (B7-3).

        Only goal approvals (``HITLGateway``) published these, so a HITL trigger
        never fired for a workflow approval gate. Called once, by the decision
        that won the claim. A workflow approval has no goal (lineage depth 0);
        its queues are ``workflow:<workflow_id>`` and ``risk:<priority>``
        (``app.governance.hitl_queues.workflow_queue_ids``), so a trigger's
        ``hitl_queue_id`` filter applies to it like to a goal approval — the
        list used to be empty and a queue-filtered trigger never fired. A
        publish failure is logged: the decision itself is already durable.
        """
        from app.governance.hitl_queues import workflow_queue_ids

        channel = {"approved": "hitl.approved", "rejected": "hitl.rejected"}.get(req.status)
        if channel is None or self._event_redis is None or not req.tenant_id:
            return
        queues = workflow_queue_ids(req.workflow_id, req.priority)
        payload = {
            "tenant_id": req.tenant_id,
            "request_id": req.request_id,
            "goal_id": "",
            "source": "workflow",
            "workflow_run_id": req.run_id,
            "workflow_id": req.workflow_id,
            "step_id": req.step_id,
            "action": req.action_taken or "",
            "approver": req.reviewed_by or "",
            "note": req.note,
            "risk_level": req.priority,
            "hitl_queue_ids": queues,
            "hitl_queue_id": queues[0] if queues else "",
        }
        try:
            from app.triggers.bus import publish_trigger_event

            await publish_trigger_event(self._event_redis, channel, payload)
        except Exception as exc:
            _log.warning(
                "workflow_hitl_trigger_event_publish_failed",
                request_id=req.request_id,
                error=str(exc)[:200],
            )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def create_workflow_approval(
        self,
        *,
        run_id: str,
        step_id: str,
        step_name: str = "",
        workflow_name: str = "",
        tenant_id: str = "",
        workflow_id: str = "",
        assignee_role: str = "",
        strategy: AssignmentStrategy = "round_robin",
        specific_user: str | None = None,
        context_payload: list[dict[str, Any]] | None = None,
        actions_config: list[dict[str, Any]] | None = None,
        deadline_hours: float | None = None,
        escalation_hours: float | None = None,
        escalation_to_role: str | None = None,
        custom_form_schema: dict[str, Any] | None = None,
        priority: Priority = "medium",
        timeout_action: TimeoutAction = "escalate",
    ) -> str:
        """Build + persist a :class:`WorkflowHITLRequest` for a suspended step.

        Bridges ``HITLStepNode.execute()``'s step-suspend call onto
        :meth:`create_request` (WS-3 fix: this method previously did not exist
        at all, so every real HITL workflow step raised ``AttributeError`` the
        first time it tried to suspend). Returns the new request's id.
        """
        # The SLA deadline is measured from the approval's own creation time
        # (deadline = created_at + the step's timeout), so the two never drift.
        created = datetime.now(UTC)
        deadline_at = None
        if deadline_hours is not None:
            deadline_at = (created + timedelta(hours=deadline_hours)).isoformat()

        req = WorkflowHITLRequest(
            created_at=created.isoformat(),
            run_id=run_id,
            workflow_id=workflow_id,
            step_id=step_id,
            step_name=step_name,
            workflow_name=workflow_name,
            tenant_id=tenant_id,
            assignment_strategy=strategy,
            assigned_to=specific_user,
            assigned_role=assignee_role or None,
            priority=priority,
            context=context_payload or [],
            actions=actions_config or [],
            custom_form_schema=custom_form_schema,
            deadline_at=deadline_at,
            timeout_action=timeout_action,
            escalation_to_role=escalation_to_role,
            escalation_after_hours=(
                escalation_hours if escalation_hours is not None else 48.0
            ),
        )
        saved = await self.create_request(req)
        return saved.request_id

    async def create_request(self, req: WorkflowHITLRequest) -> WorkflowHITLRequest:
        """Persist a new HITL request and send notifications."""
        # 1. Assign reviewer
        req = await self._assign(req)

        # 2. Persist
        await self._save(req)

        # 3. Notify
        await self._send_notification(req)

        _log.info(
            "hitl_request_created",
            request_id=req.request_id,
            run_id=req.run_id,
            priority=req.priority,
            strategy=req.assignment_strategy,
        )
        return req

    async def decide(
        self,
        request_id: str,
        action: str,
        actor_id: str,
        note: str = "",
        form_data: dict[str, Any] | None = None,
        *,
        idempotent: bool = True,
        tenant_id: str | None = None,
    ) -> WorkflowHITLRequest:
        """Submit a reviewer decision. Idempotent by default.

        ``tenant_id`` lets the decision resolve the pending approval from the
        durable (RLS-scoped) store — the path an API caller uses to decide an
        approval that a Celery worker created in another process.
        """
        req = await self.get_request(request_id, tenant_id)
        if req is None:
            raise ValueError(f"HITL request not found: {request_id}")

        if req.status != "pending":
            return self._already_decided(req, action, actor_id, idempotent)

        # Decide on a copy: the in-memory mirror hands out shared objects, and a
        # losing concurrent decision must not overwrite the winner's fields.
        req = dataclasses.replace(req)
        req.status = decision_status(action)
        req.action_taken = action
        req.reviewed_by = actor_id
        req.reviewed_at = datetime.now(UTC).isoformat()
        req.note = note
        req.form_data = form_data

        # Claim the decision atomically; only the winner resumes the run.
        if not await self._claim_decision(req):
            current = await self.get_request(request_id, tenant_id)
            if current is not None and current.status == "pending":
                # Still pending, so the claim lost on the run: it already ended.
                raise ApprovalAlreadyDecidedError(
                    current, "The workflow run is no longer waiting for this approval"
                )
            return self._already_decided(current or req, action, actor_id, idempotent)

        # The decision is durable and this call won it: fire HITL triggers once.
        await self._publish_trigger_event(req)

        # Resume the workflow
        if self._resume_callback:
            await self._resume_callback(req)

        _log.info(
            "hitl_decision_made",
            request_id=request_id,
            action=action,
            actor=actor_id,
        )
        return req

    @staticmethod
    def _already_decided(
        req: WorkflowHITLRequest, action: str, actor_id: str, idempotent: bool
    ) -> WorkflowHITLRequest:
        """A repeat of the recorded decision (same reviewer, same action) is an
        idempotent no-op; anything else is a conflict the caller must see."""
        same = req.reviewed_by == actor_id and req.action_taken == action
        if idempotent and same:
            _log.info("hitl_duplicate_decision", request_id=req.request_id, status=req.status)
            return req
        raise ApprovalAlreadyDecidedError(req)

    async def _claim_decision(self, req: WorkflowHITLRequest) -> bool:
        """Persist a decision only if the approval is still pending.

        With the durable store this is one conditional UPDATE (atomic across
        replicas) and a failure to record it is raised — a decision that is not
        durably recorded must not resume the run. Without it, a per-process
        lock serialises check-and-set.
        """
        if self._approval_store is not None and hasattr(
            self._approval_store, "decide_if_pending"
        ):
            try:
                won = bool(await self._approval_store.decide_if_pending(req))
            except Exception as exc:
                _log.error(
                    "hitl_decision_persist_failed", request_id=req.request_id, error=str(exc)
                )
                raise RuntimeError("the decision could not be recorded; try again") from exc
            if won:
                self._store[req.request_id] = req
                if self._redis is not None:
                    with contextlib.suppress(Exception):
                        await self._redis.setex(
                            f"hitl:req:{req.request_id}", 86_400 * 30, json.dumps(req.__dict__)
                        )
            return won
        async with self._decide_lock:
            local = self._store.get(req.request_id)
            if local is not None and local.status != "pending":
                return False
            await self._save(req)
            return True

    async def delegate(
        self,
        request_id: str,
        from_user: str,
        to_user: str,
        note: str = "",
        *,
        tenant_id: str | None = None,
    ) -> WorkflowHITLRequest:
        """Delegate a pending request to another user (resolved for ``tenant_id``)."""
        req = await self.get_request(request_id, tenant_id)
        if req is None:
            raise ValueError(f"HITL request not found: {request_id}")
        if req.status != "pending":
            raise ApprovalNotPendingError("Cannot delegate a non-pending request")

        entry = {
            "type": "delegation",
            "from": from_user,
            "to": to_user,
            "note": note,
            "at": datetime.now(UTC).isoformat(),
        }
        req = await self._mutate_pending(
            req, entry, {"assigned_to": to_user}, action="delegate"
        )
        await self._send_notification(req)

        _log.info(
            "hitl_delegated",
            request_id=request_id,
            from_user=from_user,
            to_user=to_user,
        )
        return req

    async def escalate(
        self,
        request_id: str,
        actor_id: str,
        note: str = "",
        *,
        tenant_id: str | None = None,
    ) -> WorkflowHITLRequest:
        """Escalate a request (manually, or by the SLA sweep)."""
        req = await self.get_request(request_id, tenant_id)
        if req is None:
            raise ValueError(f"HITL request not found: {request_id}")
        if req.status != "pending":
            raise ApprovalNotPendingError("Cannot escalate a non-pending request")

        at = datetime.now(UTC).isoformat()
        entry = {"type": "escalation", "by": actor_id, "note": note, "at": at}
        # Change assignment to escalation role
        assignment = (
            {"assigned_role": req.escalation_to_role, "assigned_to": None}
            if req.escalation_to_role
            else None
        )
        req = await self._mutate_pending(
            req, entry, assignment, action="escalate", escalated_at=at
        )
        await self._send_notification(req)
        return req

    async def bulk_decide(
        self,
        request_ids: list[str],
        action: str,
        actor_id: str,
        note: str = "",
        *,
        tenant_id: str | None = None,
    ) -> list[WorkflowHITLRequest]:
        """Decide multiple pending requests at once (resolved for ``tenant_id``,
        so worker-created approvals in the durable store are found)."""
        results = []
        for rid in request_ids:
            try:
                r = await self.decide(rid, action, actor_id, note, tenant_id=tenant_id)
                results.append(r)
            except Exception as exc:
                _log.warning("bulk_decide_item_failed", request_id=rid, error=str(exc))
        return results

    async def add_comment(
        self,
        request_id: str,
        actor_id: str,
        comment: str,
        *,
        tenant_id: str | None = None,
    ) -> WorkflowHITLRequest:
        """Add a discussion thread comment."""
        req = await self.get_request(request_id, tenant_id)
        if req is None:
            raise ValueError(f"HITL request not found: {request_id}")
        if req.status != "pending":
            raise ApprovalNotPendingError("Cannot comment on a non-pending request")

        entry = {
            "type": "comment",
            "by": actor_id,
            "text": comment,
            "at": datetime.now(UTC).isoformat(),
        }
        return await self._mutate_pending(req, entry, None, action="comment")

    async def _mutate_pending(
        self,
        req: WorkflowHITLRequest,
        entry: dict[str, Any],
        assignment: dict[str, Any] | None,
        *,
        action: str,
        escalated_at: str | None = None,
    ) -> WorkflowHITLRequest:
        """Apply a discussion entry / reassignment to a still-PENDING approval.

        With the durable store this is one conditional UPDATE that never
        writes ``status`` (WF-41), so a decision racing it can never be reverted
        to pending. ``escalated_at`` records an escalation (and bumps
        ``escalation_level``). Raises :class:`ApprovalNotPendingError` when the
        approval was decided meanwhile, :class:`ApprovalPersistenceError` when
        unwritable.
        """
        mutate = getattr(self._approval_store, "mutate_if_pending", None)
        if mutate is not None:
            extra = {"escalated_at": escalated_at} if escalated_at else {}
            try:
                updated = await mutate(
                    req.request_id,
                    req.tenant_id,
                    discussion_entry=entry,
                    assignment=assignment,
                    **extra,
                )
            except Exception as exc:
                _log.error(
                    "hitl_approval_db_save_failed", request_id=req.request_id, error=str(exc)
                )
                raise ApprovalPersistenceError(
                    "the approval could not be saved; try again"
                ) from exc
            if updated is None:
                raise ApprovalNotPendingError(
                    f"Cannot {action}: the approval was decided meanwhile"
                )
            return updated  # type: ignore[no-any-return]
        async with self._decide_lock:
            local = self._store.get(req.request_id)
            if local is not None and local.status != "pending":
                raise ApprovalNotPendingError(
                    f"Cannot {action}: the approval was decided meanwhile"
                )
            for key, value in (assignment or {}).items():
                setattr(req, key, value)
            if escalated_at:
                req.escalated_at = escalated_at
                req.escalation_level = int(req.escalation_level or 0) + 1
            req.discussion.append(entry)
            await self._save(req)
        return req

    # ── Magic link ────────────────────────────────────────────────────────────

    async def generate_magic_link(
        self,
        request_id: str,
        action: str,
        base_url: str = "https://app.agentverse.ai",
        tenant_id: str | None = None,
    ) -> str:
        """Generate a single-use magic link token bound to (request, action, tenant).

        The token is an opaque random id whose payload lives in Redis with a TTL
        and is atomically deleted on first use. Redis is REQUIRED: without it a
        token could not be validated later, so generation refuses rather than
        minting a link that either never works or (the old consume fallback)
        works for any string.
        """
        if self._redis is None:
            raise RuntimeError("magic links require Redis (single-use token store)")
        req = await self.get_request(request_id, tenant_id)
        if req is None:
            raise ValueError(f"HITL request not found: {request_id}")
        jti = secrets.token_urlsafe(32)
        payload = {
            "jti": jti,
            "request_id": request_id,
            "action": action,
            "tenant_id": req.tenant_id,
            "exp": int(time.time()) + self.MAGIC_LINK_TTL_SECONDS,
        }
        await self._redis.setex(
            f"hitl:magic:{jti}",
            self.MAGIC_LINK_TTL_SECONDS,
            json.dumps(payload),
        )

        # Update request
        req.magic_link_token = jti
        from datetime import timedelta

        req.magic_link_expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.MAGIC_LINK_TTL_SECONDS)
        ).isoformat()
        await self._save(req)

        # The route is mounted under /api/v1 (router_hitl prefix /approvals).
        return f"{base_url.rstrip('/')}/api/v1/approvals/magic/{jti}?action={action}"

    async def consume_magic_link(self, token: str) -> dict[str, Any] | None:
        """Validate and consume a magic link token (single-use).

        Returns the stored payload on success, None if unknown/expired/used.

        Old bug: without Redis this returned ``{"token": token, "valid": True}``
        for ANY string, so anyone could hit /approvals/magic/<garbage>. It now
        fails closed: no Redis -> no valid tokens. The read+delete is atomic
        (GETDEL) so two concurrent clicks cannot both consume one token.
        """
        if self._redis is None or not token:
            if self._redis is None:
                _log.warning("hitl_magic_link_rejected_no_redis")
            return None
        redis_key = f"hitl:magic:{token}"
        getdel = getattr(self._redis, "getdel", None)
        if getdel is not None:
            raw = await getdel(redis_key)
        else:  # pragma: no cover - very old redis client without GETDEL
            raw = await self._redis.get(redis_key)
            if raw is not None:
                await self._redis.delete(redis_key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or not payload.get("request_id"):
            return None
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload

    # ── Query ─────────────────────────────────────────────────────────────────

    async def get_request(
        self, request_id: str, tenant_id: str | None = None
    ) -> WorkflowHITLRequest | None:
        """Load a HITL request by ID.

        With a ``tenant_id`` and the durable store wired, the read is served from
        Postgres (RLS-scoped) — the path that makes a worker-created approval
        visible to the API process. Without a tenant (the legacy signature) it
        falls back to Redis / the in-memory mirror.
        """
        if self._approval_store is not None and tenant_id:
            try:
                found = await self._approval_store.get(request_id, tenant_id)
            except Exception as exc:
                _log.warning("hitl_approval_db_get_failed", request_id=request_id, error=str(exc))
                found = None
            if found is not None:
                return found
        found_local: WorkflowHITLRequest | None = None
        if self._redis is not None:
            raw = await self._redis.get(f"hitl:req:{request_id}")
            if raw:
                found_local = WorkflowHITLRequest(**json.loads(raw))
        if found_local is None:
            found_local = self._store.get(request_id)
        # The Redis / in-memory mirrors are keyed by request id alone: never hand
        # one tenant's approval to a caller from another tenant.
        if found_local is not None and tenant_id and found_local.tenant_id != tenant_id:
            return None
        return found_local

    async def check_and_escalate_overdue(
        self,
        *,
        now: datetime | None = None,
        candidates: list[Any] | None = None,
        limit: int = 200,
    ) -> dict[str, int]:
        """SLA sweep: apply ``timeout_action`` to pending approvals past their deadline.

        A request is overdue once its deadline (``deadline_at`` = created_at +
        the step's timeout, else created_at + ``escalation_after_hours``) has
        passed. Each overdue request gets its author's ``timeout_action``
        exactly once (see :meth:`apply_timeout_action`):

        * ``escalate`` — reassign to ``escalation.to_role`` and notify; the
          approval stays pending and the run keeps waiting (never proceeds);
        * ``auto_reject`` / ``auto_approve`` — decided by ``sla-timeout``,
          audited, and the run resumes down the rejection / approval path;
        * ``pause`` — the run is paused; the approval stays pending.

        Old bug: only ``escalate`` was acted on — auto_approve / auto_reject /
        pause were accepted by the DSL and silently did nothing. Candidates come
        from the durable store's indexed overdue scan (maintenance role, at most
        ``limit`` per sweep, most overdue first), else this process's mirror.
        """
        current = now or datetime.now(UTC)
        if candidates is None:
            candidates = await self._overdue_candidates(current, limit)
        counts = {
            "checked": len(candidates),
            "escalated": 0,
            "auto_rejected": 0,
            "auto_approved": 0,
            "paused": 0,
            "expired": 0,
            "skipped": 0,
            "failed": 0,
        }
        for req in candidates:
            if (
                getattr(req, "status", "") != "pending"
                or not _is_overdue(req, current)
                or _timeout_handled(req)
            ):
                continue
            try:
                outcome = await self.apply_timeout_action(req, now=current)
            except Exception as exc:
                counts["failed"] += 1
                _log.error(
                    "hitl_sla_timeout_failed",
                    request_id=getattr(req, "request_id", ""),
                    timeout_action=getattr(req, "timeout_action", ""),
                    error=str(exc)[:300],
                )
                continue
            counts[outcome if outcome in counts else "skipped"] += 1
        return counts

    async def _overdue_candidates(self, now: datetime, limit: int) -> list[Any]:
        """Overdue pending approvals, most overdue first, at most ``limit``."""
        store = self._approval_store
        overdue = getattr(store, "list_overdue_pending", None)
        if overdue is not None:
            return list(await overdue(now=now, limit=limit))
        scan = getattr(store, "list_pending_all_tenants", None)
        pool = (
            list(await scan(limit=max(limit, 500)))
            if scan is not None
            else [r for r in self._store.values() if r.status == "pending"]
        )
        due = [r for r in pool if _is_overdue(r, now) and not _timeout_handled(r)]
        due.sort(key=lambda r: effective_deadline(r) or now)
        return due[:limit]

    async def apply_timeout_action(self, req: WorkflowHITLRequest, *, now: datetime) -> str:
        """Apply ``req.timeout_action`` once, race-safe with human decisions.

        Returns the outcome: ``escalated`` / ``auto_rejected`` /
        ``auto_approved`` / ``paused`` / ``escalation_disallowed`` / ``expired``
        (its run already ended), or ``not_pending`` (a human decided — or
        another sweep timed it out — first: nothing done) / ``deferred`` (pause
        before the run finished suspending: retried next sweep).
        """
        plan = self._timeout_plan(req, now)
        result, updated = await self._claim_timeout(req, plan, now)
        if result == CLAIM_RUN_ENDED:
            _log.info("hitl_sla_timeout_run_ended", request_id=req.request_id, run_id=req.run_id)
            return "expired"
        if result == CLAIM_RUN_NOT_WAITING:
            return "deferred"
        if result != CLAIM_APPLIED or updated is None:
            _log.info("hitl_sla_timeout_lost_race", request_id=req.request_id)
            return "not_pending"

        log_fields = {
            "request_id": req.request_id,
            "run_id": req.run_id,
            "step_id": req.step_id,
            "tenant_id": req.tenant_id,
            "deadline_at": req.deadline_at,
            "timeout_action": req.timeout_action,
        }
        if plan.outcome == "escalated":
            _log.info("hitl_sla_escalated", to_role=req.escalation_to_role, **log_fields)
            await self._send_notification(updated)
        elif plan.status is not None:
            if plan.outcome == "auto_approved":
                # An explicit author opt-in, but the run proceeds with nobody
                # having looked at it: always loud.
                _log.warning("hitl_sla_auto_approved", **log_fields)
            else:
                _log.info("hitl_sla_auto_rejected", **log_fields)
            # Same as a human decision: HITL triggers fire, then the run resumes.
            await self._publish_trigger_event(updated)
            if self._resume_callback is None:
                _log.error("hitl_sla_decision_no_resume_callback", **log_fields)
            else:
                try:
                    await self._resume_callback(updated)
                except Exception as exc:
                    _log.error("hitl_sla_resume_failed", error=str(exc)[:300], **log_fields)
                    raise
        elif plan.outcome == "paused":
            _log.info("hitl_sla_run_paused", **log_fields)
        else:
            _log.warning("hitl_sla_escalation_disallowed", **log_fields)
        return plan.outcome

    @staticmethod
    def _timeout_decision_action(req: WorkflowHITLRequest, *, approve: bool) -> str:
        """The action id an automatic decision takes: the step's own approve /
        reject action when it declares one (so its ``next`` branch is honoured),
        else the canonical ``approve`` / ``reject``."""
        from app.workflow.steps.hitl_step import APPROVE_ACTIONS, REJECT_ACTIONS

        family = APPROVE_ACTIONS if approve else REJECT_ACTIONS
        for action in req.actions or []:
            action_id = str((action or {}).get("id") or "") if isinstance(action, dict) else ""
            if action_id.strip().lower() in family:
                return action_id
        return "approve" if approve else "reject"

    def _timeout_plan(self, req: WorkflowHITLRequest, now: datetime) -> TimeoutPlan:
        at = now.isoformat()
        action = req.timeout_action if req.timeout_action in _TIMEOUT_ACTIONS else "escalate"
        deadline = req.deadline_at or (
            dl.isoformat() if (dl := effective_deadline(req)) is not None else ""
        )
        base_note = f"request_id={req.request_id}; deadline_at={deadline}; timeout_action={action}"
        entry: dict[str, Any] = {
            "type": "timeout",
            "action": action,
            "by": SLA_TIMEOUT_ACTOR,
            "at": at,
            "deadline_at": deadline,
        }
        if action == "escalate":
            if not req.allow_escalate:
                outcome = "escalation_disallowed"
                return TimeoutPlan(
                    outcome=outcome,
                    patch={"timed_out_at": at, "timeout_outcome": outcome},
                    entry={**entry, "note": "Deadline passed; escalation is disabled"},
                    audit_note=base_note,
                )
            assignment = (
                {"assigned_role": req.escalation_to_role, "assigned_to": None}
                if req.escalation_to_role
                else None
            )
            return TimeoutPlan(
                outcome="escalated",
                patch={
                    "timed_out_at": at,
                    "timeout_outcome": "escalated",
                    "escalated_at": at,
                    **(assignment or {}),
                },
                entry={
                    "type": "escalation",
                    "by": SLA_TIMEOUT_ACTOR,
                    "note": "SLA deadline passed",
                    "at": at,
                    "to_role": req.escalation_to_role,
                    "deadline_at": deadline,
                },
                assignment=assignment,
                bump_escalation=True,
                audit_note=f"{base_note}; to_role={req.escalation_to_role or ''}",
            )
        if action in ("auto_reject", "auto_approve"):
            approve = action == "auto_approve"
            outcome = "auto_approved" if approve else "auto_rejected"
            act = self._timeout_decision_action(req, approve=approve)
            status = decision_status(act)
            note = (
                f"Automatically {'approved' if approve else 'rejected'}: no decision "
                f"before the deadline ({deadline}); timeout_action={action}"
            )
            return TimeoutPlan(
                outcome=outcome,
                patch={
                    "timed_out_at": at,
                    "timeout_outcome": outcome,
                    "status": status,
                    "action_taken": act,
                    "reviewed_by": SLA_TIMEOUT_ACTOR,
                    "reviewed_at": at,
                    "note": note,
                },
                entry={**entry, "decision": act},
                status=status,
                audit_note=f"{base_note}; action={act}",
            )
        return TimeoutPlan(
            outcome="paused",
            patch={"timed_out_at": at, "timeout_outcome": "paused"},
            entry={**entry, "note": "Deadline passed; the run is paused"},
            pause_run=True,
            audit_note=base_note,
        )

    async def _claim_timeout(
        self, req: WorkflowHITLRequest, plan: TimeoutPlan, now: datetime
    ) -> tuple[str, WorkflowHITLRequest | None]:
        """Compare-and-set ``plan`` onto a still-pending, not-yet-timed-out approval.

        The durable store does it in one transaction with the run update and
        the audit row (atomic across replicas, and against a human decision's
        conditional UPDATE). Without it, the per-process lock that serialises
        :meth:`decide` serialises this too.
        """
        claim = getattr(self._approval_store, "claim_timeout", None)
        if claim is not None:
            result, updated = await claim(req, plan, now=now)
            if updated is not None:
                self._store[req.request_id] = updated
                if self._redis is not None:
                    with contextlib.suppress(Exception):
                        await self._redis.setex(
                            f"hitl:req:{req.request_id}",
                            86_400 * 30,
                            json.dumps(updated.__dict__),
                        )
            return str(result), updated
        async with self._decide_lock:
            local = self._store.get(req.request_id)
            current = local if local is not None else req
            if current.status != "pending" or _timeout_handled(current):
                return CLAIM_NOT_PENDING, None
            updated = dataclasses.replace(current, discussion=[*current.discussion, plan.entry])
            for key, value in plan.patch.items():
                setattr(updated, key, value)
            if plan.bump_escalation:
                updated.escalation_level = int(current.escalation_level or 0) + 1
            if plan.status is not None:
                updated.status = plan.status
            await self._save(updated)
        return CLAIM_APPLIED, updated

    async def list_pending(
        self,
        tenant_id: str,
        assigned_to: str | None = None,
        priority: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[WorkflowHITLRequest], int]:
        """List pending HITL requests for a tenant."""
        if self._approval_store is not None:
            try:
                return await self._approval_store.list_pending(
                    tenant_id,
                    assigned_to=assigned_to,
                    priority=priority,
                    page=page,
                    per_page=per_page,
                )
            except Exception as exc:
                _log.warning("hitl_approval_db_list_failed", tenant_id=tenant_id, error=str(exc))
        all_items = list(self._store.values())
        filtered = [
            r
            for r in all_items
            if r.tenant_id == tenant_id
            and r.status == "pending"
            and (assigned_to is None or r.assigned_to == assigned_to)
            and (priority is None or r.priority == priority)
        ]
        # Sort by priority then deadline
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        filtered.sort(key=lambda r: (priority_order.get(r.priority, 99), r.created_at))

        total = len(filtered)
        start = (page - 1) * per_page
        return filtered[start : start + per_page], total

    async def get_stats(self, tenant_id: str) -> dict[str, Any]:
        """Return inbox stats for a tenant."""
        if self._approval_store is not None:
            try:
                return await self._approval_store.get_stats(tenant_id)
            except Exception as exc:
                _log.warning("hitl_approval_db_stats_failed", tenant_id=tenant_id, error=str(exc))
        items = [r for r in self._store.values() if r.tenant_id == tenant_id]
        pending = sum(1 for r in items if r.status == "pending")
        decided = [r for r in items if r.reviewed_at]
        avg_resolution = 0.0
        if decided:
            durations = []
            for r in decided:
                try:
                    created = datetime.fromisoformat(r.created_at)
                    reviewed = datetime.fromisoformat(r.reviewed_at)  # type: ignore[arg-type]
                    durations.append((reviewed - created).total_seconds())
                except Exception:
                    pass
            avg_resolution = sum(durations) / len(durations) if durations else 0.0

        return {
            "pending_count": pending,
            "total_requests": len(items),
            "avg_resolution_seconds": avg_resolution,
        }

    # ── Assignment ────────────────────────────────────────────────────────────

    async def _assign(self, req: WorkflowHITLRequest) -> WorkflowHITLRequest:
        strategy = req.assignment_strategy
        if strategy == "specific_user" and req.assigned_to:
            pass  # Already set
        elif strategy == "round_robin":
            # Simple round-robin across available reviewers in store
            req.assigned_to = await self._round_robin_pick(req.assigned_role, req.tenant_id)
        elif strategy == "least_busy":
            req.assigned_to = await self._least_busy_pick(req.assigned_role, req.tenant_id)
        elif strategy == "skill_based":
            req.assigned_to = await self._skill_based_pick(req.assigned_role, req.tenant_id)
        return req

    async def _role_load(
        self, role: str | None, tenant_id: str
    ) -> tuple[list[str], dict[str, tuple[int, datetime | None]]]:
        """Members of ``role`` and their shared (DB) approval load.

        Empty when there is no role or no durable store — the approval then stays
        role-/un-assigned and any eligible reviewer may take it (never a guess
        from this process's memory, which other replicas can't see)."""
        store = self._approval_store
        if not role or not tenant_id or store is None or not hasattr(store, "role_members"):
            return [], {}
        try:
            members = list(await store.role_members(tenant_id, role))
            load = dict(await store.assignee_load(tenant_id, members)) if members else {}
        except Exception as exc:
            _log.warning("hitl_assignment_lookup_failed", role=role, error=str(exc))
            return [], {}
        return members, load

    async def _round_robin_pick(self, role: str | None, tenant_id: str) -> str | None:
        """Rotate through the role's members: the one assigned least recently
        (never-assigned first, then by id). Derived from the shared approvals
        table, so the rotation holds across replicas.

        Old bug: always returned None, so the default strategy assigned nobody."""
        members, load = await self._role_load(role, tenant_id)
        if not members:
            return None
        epoch = datetime.min.replace(tzinfo=UTC)

        def last_assigned(user: str) -> datetime:
            at = load.get(user, (0, None))[1]
            if at is None:
                return epoch
            return at if at.tzinfo else at.replace(tzinfo=UTC)

        return min(members, key=lambda u: (last_assigned(u), members.index(u)))

    async def _least_busy_pick(self, role: str | None, tenant_id: str) -> str | None:
        """The role member with the fewest pending approvals (shared DB count).

        Old bug: counted only this process's in-memory approvals, across every
        assignee regardless of role."""
        members, load = await self._role_load(role, tenant_id)
        if not members:
            return None
        return min(members, key=lambda u: (load.get(u, (0, None))[0], members.index(u)))

    async def _skill_based_pick(self, role: str | None, tenant_id: str) -> str | None:
        """No skills registry exists: publishing a ``skill_based`` approval step is
        refused (see ``WorkflowService.publish``). A definition that still reaches
        here leaves the approval with its role so any member can take it."""
        _log.warning("hitl_skill_based_unsupported", role=role, tenant_id=tenant_id)
        return None

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _save(self, req: WorkflowHITLRequest) -> None:
        """Persist ``req``; raises :class:`ApprovalPersistenceError` when the
        durable store is wired and the write fails (fail closed — the caller's
        step fails / the API answers 503 instead of reporting a phantom change).
        """
        if self._approval_store is not None:
            try:
                await self._approval_store.save(req)
            except Exception as exc:
                _log.error(
                    "hitl_approval_db_save_failed",
                    request_id=req.request_id,
                    error=str(exc),
                )
                raise ApprovalPersistenceError(
                    "the approval could not be saved; try again"
                ) from exc
        self._store[req.request_id] = req
        if self._redis is not None:
            payload = json.dumps(req.__dict__)
            if self._approval_store is None:
                # Redis is the only shared copy: its failure must surface.
                await self._redis.setex(f"hitl:req:{req.request_id}", 86_400 * 30, payload)
            else:
                # Postgres holds the approval; the Redis mirror is a cache.
                with contextlib.suppress(Exception):
                    await self._redis.setex(f"hitl:req:{req.request_id}", 86_400 * 30, payload)

    async def _send_notification(self, req: WorkflowHITLRequest) -> None:
        if self._notify is None:
            return
        body = f"Run {req.run_id} step {req.step_id} needs your approval"
        # One-tap approve/reject links, minted only when the single-use token
        # store (Redis) is wired — otherwise the notification carries no links
        # rather than links that cannot be validated.
        if self._redis is not None:
            try:
                base = "http://localhost:5173"
                with contextlib.suppress(Exception):
                    from app.core.config import get_settings

                    base = get_settings().public_base_url or base
                approve = await self.generate_magic_link(
                    req.request_id, "approved", base_url=base, tenant_id=req.tenant_id
                )
                reject = await self.generate_magic_link(
                    req.request_id, "rejected", base_url=base, tenant_id=req.tenant_id
                )
                body += f"\nApprove: {approve}\nReject: {reject}"
            except Exception as exc:
                _log.warning("hitl_magic_link_generation_failed", error=str(exc))
        try:
            await self._notify.send(
                user_id=req.assigned_to,
                subject="Action Required: Workflow approval",
                body=body,
                priority=req.priority,
            )
        except Exception as exc:
            _log.warning("hitl_notification_failed", error=str(exc))


# ── Context builder helpers ───────────────────────────────────────────────────


def make_context_item(
    display_type: Literal["image", "json", "table", "diff", "chart", "number", "list"],
    title: str,
    data: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a rich context item dict for a HITL request."""
    item: dict[str, Any] = {
        "display_type": display_type,
        "title": title,
        "data": data,
    }
    item.update(kwargs)
    return item


def make_number_context(
    title: str,
    value: float | int,
    unit: str = "",
    threshold_yellow: float | None = None,
    threshold_red: float | None = None,
) -> dict[str, Any]:
    """Create a number display context item with optional risk thresholds."""
    return make_context_item(
        display_type="number",
        title=title,
        data={"value": value, "unit": unit},
        threshold_yellow=threshold_yellow,
        threshold_red=threshold_red,
    )
