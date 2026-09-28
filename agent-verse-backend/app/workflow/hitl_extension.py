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
 10. Deadline tracking + SLA countdown
 11. timeout_action: auto_approve | auto_reject | escalate | pause
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

import contextlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


_SLA_ACTOR = "system:sla"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_overdue(req: Any, now: datetime) -> bool:
    deadline = _parse_ts(getattr(req, "deadline_at", None))
    if deadline is not None:
        return now >= deadline
    created = _parse_ts(getattr(req, "created_at", None))
    hours = float(getattr(req, "escalation_after_hours", 0) or 0)
    if created is None or hours <= 0:
        return False
    return (now - created).total_seconds() >= hours * 3600


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
    ) -> None:
        self._base = base_gateway
        self._redis = redis_client
        self._notify = notification_service
        self._resume_callback = resume_callback
        # Durable, cross-process store (Postgres, RLS). When set, a pending
        # approval created by an out-of-process Celery worker is visible to the
        # API's /approvals endpoints and vice versa (gap #2: cross-process HITL).
        self._approval_store = approval_store
        # In-memory fallback (dev/tests, and process-local mirror).
        self._store: dict[str, WorkflowHITLRequest] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def create_workflow_approval(
        self,
        *,
        run_id: str,
        step_id: str,
        step_name: str = "",
        workflow_name: str = "",
        tenant_id: str = "",
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
        from datetime import timedelta

        deadline_at = None
        if deadline_hours is not None:
            deadline_at = (datetime.now(UTC) + timedelta(hours=deadline_hours)).isoformat()

        req = WorkflowHITLRequest(
            run_id=run_id,
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

        if idempotent and req.status != "pending":
            _log.info("hitl_duplicate_decision", request_id=request_id, status=req.status)
            return req

        req.status = action if action in ("approved", "rejected") else "decided"
        req.action_taken = action
        req.reviewed_by = actor_id
        req.reviewed_at = datetime.now(UTC).isoformat()
        req.note = note
        req.form_data = form_data

        await self._save(req)

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
            raise ValueError("Cannot delegate a non-pending request")

        req.assigned_to = to_user
        req.discussion.append(
            {
                "type": "delegation",
                "from": from_user,
                "to": to_user,
                "note": note,
                "at": datetime.now(UTC).isoformat(),
            }
        )

        await self._save(req)
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

        req.discussion.append(
            {
                "type": "escalation",
                "by": actor_id,
                "note": note,
                "at": datetime.now(UTC).isoformat(),
            }
        )

        # Change assignment to escalation role
        if req.escalation_to_role:
            req.assigned_role = req.escalation_to_role
            req.assigned_to = None

        await self._save(req)
        await self._send_notification(req)
        return req

    async def bulk_decide(
        self,
        request_ids: list[str],
        action: str,
        actor_id: str,
        note: str = "",
    ) -> list[WorkflowHITLRequest]:
        """Decide multiple pending requests at once."""
        results = []
        for rid in request_ids:
            try:
                r = await self.decide(rid, action, actor_id, note)
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

        req.discussion.append(
            {
                "type": "comment",
                "by": actor_id,
                "text": comment,
                "at": datetime.now(UTC).isoformat(),
            }
        )

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
        self, *, now: datetime | None = None, candidates: list[Any] | None = None
    ) -> dict[str, int]:
        """SLA sweep: escalate pending approvals past their deadline.

        The beat task ``workflow.check_hitl_escalations`` called this method,
        which did not exist — so no approval was ever auto-escalated. A request
        is overdue when ``deadline_at`` has passed, or (without a deadline) when
        it has been pending longer than ``escalation_after_hours``. Only
        ``timeout_action == "escalate"`` is acted on, once per request;
        auto_approve / auto_reject / pause are NOT IMPLEMENTED and are counted as
        ``skipped``. Candidates come from the durable store's cross-tenant scan
        (maintenance role) when wired, else this process's mirror.
        """
        current = now or datetime.now(UTC)
        if candidates is None:
            scan = getattr(self._approval_store, "list_pending_all_tenants", None)
            candidates = list(await scan()) if scan is not None else [
                r for r in self._store.values() if r.status == "pending"
            ]
        escalated = skipped = 0
        for req in candidates:
            if getattr(req, "status", "") != "pending" or not _is_overdue(req, current):
                continue
            if req.timeout_action != "escalate" or not req.allow_escalate:
                skipped += 1
                continue
            if any(
                d.get("type") == "escalation" and d.get("by") == _SLA_ACTOR
                for d in req.discussion
            ):
                continue  # already auto-escalated once
            try:
                await self.escalate(
                    req.request_id, _SLA_ACTOR, note="SLA deadline passed",
                    tenant_id=req.tenant_id,
                )
                escalated += 1
            except Exception as exc:
                _log.warning(
                    "hitl_sla_escalation_failed", request_id=req.request_id, error=str(exc)
                )
        return {"checked": len(candidates), "escalated": escalated, "skipped": skipped}

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

    async def _round_robin_pick(self, role: str | None, tenant_id: str) -> str | None:
        """Simple round-robin — for now returns None (DB-backed in production)."""
        return None

    async def _least_busy_pick(self, role: str | None, tenant_id: str) -> str | None:
        """Picks reviewer with fewest pending requests."""
        if not self._store:
            return None
        pending_by_user: dict[str, int] = {}
        for r in self._store.values():
            if r.tenant_id == tenant_id and r.status == "pending" and r.assigned_to:
                pending_by_user[r.assigned_to] = pending_by_user.get(r.assigned_to, 0) + 1
        if not pending_by_user:
            return None
        return min(pending_by_user, key=lambda u: pending_by_user[u])

    async def _skill_based_pick(self, role: str | None, tenant_id: str) -> str | None:
        """Stub — hooks into a skills registry in production."""
        return None

    # ── Persistence ───────────────────────────────────────────────────────────

    async def _save(self, req: WorkflowHITLRequest) -> None:
        self._store[req.request_id] = req
        # Durable, cross-process persistence (Postgres, RLS). Best-effort: an
        # infra hiccup must not break the in-process suspend/resume path.
        if self._approval_store is not None:
            try:
                await self._approval_store.save(req)
            except Exception as exc:
                _log.warning(
                    "hitl_approval_db_save_failed",
                    request_id=req.request_id,
                    error=str(exc),
                )
        if self._redis is not None:
            await self._redis.setex(
                f"hitl:req:{req.request_id}",
                86_400 * 30,  # 30-day TTL
                json.dumps(req.__dict__),
            )

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
