"""Human-In-The-Loop (HITL) gateway — approval request lifecycle.

When the permission matrix returns APPROVAL or the pipeline detects high risk,
the agent pauses and creates an ApprovalRequest. A human operator approves or
rejects it via the API. On approval the agent resumes; on rejection it either
retries with a different approach or marks the goal as failed.

In production ApprovalRequests are stored in PostgreSQL and notifications are
sent via Slack/email. This in-memory version is used in tests.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

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


class HITLGateway:
    """In-memory HITL gateway, namespaced per tenant."""

    def __init__(self) -> None:
        # Key: (tenant_id, request_id) → ApprovalRequest
        self._requests: dict[tuple[str, str], ApprovalRequest] = {}

    def request_approval(
        self,
        *,
        goal_id: str,
        action: str,
        risk_level: str,
        tenant_ctx: TenantContext,
    ) -> str:
        req = ApprovalRequest(goal_id=goal_id, action=action, risk_level=risk_level)
        self._requests[(tenant_ctx.tenant_id, req.request_id)] = req
        return req.request_id

    def get_request(
        self, request_id: str, *, tenant_ctx: TenantContext
    ) -> ApprovalRequest | None:
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
        if req is None:
            return False
        req.status = ApprovalStatus.APPROVED
        req.approver = approver
        req.note = note
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
        if req is None:
            return False
        req.status = ApprovalStatus.REJECTED
        req.approver = approver
        req.note = note
        return True

    def list_pending(self, *, tenant_ctx: TenantContext) -> list[ApprovalRequest]:
        return [
            req
            for (tid, _), req in self._requests.items()
            if tid == tenant_ctx.tenant_id and req.status == ApprovalStatus.PENDING
        ]
