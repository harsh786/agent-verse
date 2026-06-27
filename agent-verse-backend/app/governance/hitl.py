"""Human-In-The-Loop gateway with asyncio.Event-based blocking.

When the agent loop creates an approval request, it waits on an asyncio.Event.
When a human approves/rejects via the API, the event is set and the agent resumes.
Timeout escalation: after `timeout_seconds`, the request auto-rejects.
"""
from __future__ import annotations

import asyncio
import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.tenancy.context import TenantContext


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


@dataclass
class ApprovalRequest:
    goal_id: str
    action: str
    risk_level: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: ApprovalStatus = ApprovalStatus.PENDING
    approver: str | None = None
    note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    required_approvers: int = 1
    approvals_received: int = 0
    approvers_list: list[str] = field(default_factory=list)
    # asyncio.Event set when approved/rejected; created in running event loop context
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    # In-process expiry datetime (not persisted to DB directly)
    _expires_at_dt: Any = field(default=None, repr=False, compare=False)


class HITLGateway:
    """Async-capable HITL gateway with blocking wait and timeout escalation."""

    DEFAULT_TIMEOUT = 300.0  # 5 minutes default

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT) -> None:
        # Key: (tenant_id, request_id) -> ApprovalRequest
        self._requests: dict[tuple[str, str], ApprovalRequest] = {}
        self._timeout = timeout_seconds
        self._notification_service: Any = None
        self._db_session_factory: Any = None

    def request_approval(
        self,
        *,
        goal_id: str,
        action: str,
        risk_level: str,
        tenant_ctx: TenantContext,
    ) -> str:
        """Create an approval request and return its ID (non-blocking).

        Also sets _expires_at_dt for in-process timeout enforcement.
        """
        from datetime import UTC, timedelta
        req = ApprovalRequest(goal_id=goal_id, action=action, risk_level=risk_level)
        req._expires_at_dt = datetime.now(UTC) + timedelta(seconds=self._timeout)
        self._requests[(tenant_ctx.tenant_id, req.request_id)] = req

        # Persist to DB (fire-and-forget, Fix 4)
        if self._db_session_factory is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._db_persist_approval_request(req, tenant_ctx.tenant_id)
                )
            except RuntimeError:
                pass  # No running loop (shouldn't happen in async context)

        # Dispatch notification (fire and forget)
        if self._notification_service is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._notification_service.notify_approval_required(
                        request_id=req.request_id,
                        goal_id=goal_id,
                        action=action,
                        risk_level=risk_level,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                )
            except RuntimeError:
                pass  # No running loop (shouldn't happen in async context)

        return req.request_id

    async def _db_persist_approval_request(
        self, req: ApprovalRequest, tenant_id: str
    ) -> None:
        """Persist new approval request to DB (Fix 4)."""
        if self._db_session_factory is None:
            return
        try:
            from sqlalchemy import text
            async with self._db_session_factory() as session, session.begin():
                await session.execute(
                    text(
                        """INSERT INTO approval_requests
                            (id, tenant_id, goal_id, action, risk_level, status, created_at)
                            VALUES (:id, :tid, :gid, :action, :risk, 'pending', NOW())
                            ON CONFLICT (id) DO NOTHING"""
                    ),
                    {
                        "id": req.request_id,
                        "tid": tenant_id,
                        "gid": req.goal_id,
                        "action": req.action,
                        "risk": req.risk_level,
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("hitl_db_persist_failed", error=str(exc))

    async def wait_for_approval(
        self,
        request_id: str,
        *,
        tenant_ctx: TenantContext,
        timeout: float | None = None,
    ) -> ApprovalStatus:
        """Block until the request is resolved or timeout expires.

        Returns the final ApprovalStatus (APPROVED, REJECTED, or TIMED_OUT).
        """
        req = self._requests.get((tenant_ctx.tenant_id, request_id))
        if req is None:
            return ApprovalStatus.REJECTED

        timeout_s = timeout if timeout is not None else self._timeout
        try:
            await asyncio.wait_for(req._event.wait(), timeout=timeout_s)
        except TimeoutError:
            req.status = ApprovalStatus.TIMED_OUT
            req._event.set()  # Unblock any other waiters

        return req.status

    def get_request(self, request_id: str, *, tenant_ctx: TenantContext) -> ApprovalRequest | None:
        return self._requests.get((tenant_ctx.tenant_id, request_id))

    def approve(
        self,
        request_id: str,
        *,
        approver: str,
        note: str = "",
        tenant_ctx: TenantContext,
    ) -> bool:
        req = self.get_request(request_id, tenant_ctx=tenant_ctx)
        if req is None or req.status != ApprovalStatus.PENDING:
            return False
        # Track approvers (prevent duplicate votes)
        if approver not in req.approvers_list:
            req.approvers_list.append(approver)
            req.approvals_received += 1
        req.approver = approver  # Last approver
        req.note = note
        # Only set APPROVED when threshold reached
        if req.approvals_received >= req.required_approvers:
            req.status = ApprovalStatus.APPROVED
            req._event.set()  # Unblock waiting agent
        return True

    def reject(
        self,
        request_id: str,
        *,
        approver: str,
        note: str = "",
        tenant_ctx: TenantContext,
    ) -> bool:
        req = self.get_request(request_id, tenant_ctx=tenant_ctx)
        if req is None or req.status != ApprovalStatus.PENDING:
            return False
        req.status = ApprovalStatus.REJECTED
        req.approver = approver
        req.note = note
        req._event.set()  # Unblock waiting agent
        return True

    def list_pending(self, *, tenant_ctx: TenantContext) -> list[ApprovalRequest]:
        return [
            req
            for (tid, _), req in self._requests.items()
            if tid == tenant_ctx.tenant_id and req.status == ApprovalStatus.PENDING
        ]

    def expire_timed_out_requests(self) -> list[str]:
        """Check all pending requests and auto-reject those past _expires_at_dt."""
        from datetime import UTC
        expired = []
        now = datetime.now(UTC)
        for (tenant_id, req_id), req in list(self._requests.items()):
            if req.status != ApprovalStatus.PENDING:
                continue
            expires_at = getattr(req, "_expires_at_dt", None)
            if expires_at and now > expires_at:
                req.status = ApprovalStatus.TIMED_OUT
                req._event.set()
                expired.append(req_id)
        return expired

    async def load_pending_from_db(self, db: Any, tenant_id: str) -> int:
        """Restore pending approvals from DB on startup (so goals can resume)."""
        if db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.governance import ApprovalRequest as DBApprovalReq
            async with db() as session:
                result = await session.execute(
                    select(DBApprovalReq)
                    .where(DBApprovalReq.tenant_id == tenant_id,
                           DBApprovalReq.status == "pending")
                )
                rows = result.scalars().all()
            for row in rows:
                req = ApprovalRequest(
                    goal_id=row.goal_id,
                    action=row.action or "unknown",
                    risk_level=row.risk_level or "unknown",
                    request_id=row.id,
                    status=ApprovalStatus.PENDING,
                )
                self._requests[(tenant_id, row.id)] = req
            return len(rows)
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("hitl_load_from_db_failed", error=str(exc))
            return 0

    async def load_pending_from_db_full(self, db: Any) -> int:
        """Load all pending requests from DB on startup (full tenant scan).

        Returns the number of requests loaded. Returns 0 immediately when db is None.
        """
        if db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.governance import ApprovalRequest as DBApprovalReq
            async with db() as session:
                result = await session.execute(
                    select(DBApprovalReq).where(DBApprovalReq.status == "pending")
                )
                rows = result.scalars().all()
            for row in rows:
                req = ApprovalRequest(
                    goal_id=row.goal_id,
                    action=row.action or "unknown",
                    risk_level=row.risk_level or "unknown",
                    request_id=row.id,
                    status=ApprovalStatus.PENDING,
                )
                tenant_id = getattr(row, "tenant_id", "unknown")
                self._requests[(tenant_id, row.id)] = req
            return len(rows)
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("hitl_load_pending_full_failed", error=str(exc))
            return 0

    async def startup_restore(self, db: Any) -> int:
        """Restore pending HITL approval requests from DB on startup.

        This MUST be called after DB session factory is available so that:
        - Goals that were waiting_human before restart can be resumed when approved
        - Operators who approved via Slack/UI during downtime have their approval processed

        Returns: number of requests restored
        """
        if db is None:
            return 0
        try:
            count = await self.load_pending_from_db_full(db)
            from app.observability.logging import get_logger
            get_logger(__name__).info(
                "hitl_pending_restored",
                count=count,
                message=f"Restored {count} pending HITL approval requests from DB",
            )
            return count
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("hitl_startup_restore_failed", error=str(exc))
            return 0
