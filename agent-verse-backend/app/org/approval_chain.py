"""Cross-department approval chain engine.

Defines approval chains for high-risk actions (e.g. prod deployment, large spend)
and provides runtime methods to create/check/escalate approval requests.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ApprovalChain:
    id: str
    name: str
    trigger_pattern: str        # keyword pattern or exact action name
    required_roles: list[str]   # role names or dept_kind:role patterns
    strategy: str               # "all" | "any" | "majority"
    timeout_hours: float
    escalation_path: list[str]  # roles to escalate to on timeout
    requires_human: bool
    risk_threshold: str         # minimum risk level: "low"|"medium"|"high"|"critical"
    description: str = ""


@dataclass
class ApprovalRequest:
    request_id: str
    chain_id: str
    action: str
    action_detail: str
    mission_id: str | None
    agent_id: str | None
    tenant_id: str
    org_id: str | None
    approvers_needed: list[str]   # role names required
    approvers_responded: list[str] = field(default_factory=list)
    approved_by: list[str] = field(default_factory=list)
    rejected_by: list[str] = field(default_factory=list)
    status: str = "pending"       # pending|approved|rejected|escalated|expired|cancelled
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    escalated_at: datetime | None = None
    resolved_at: datetime | None = None
    notes: str = ""

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at

    @property
    def is_resolved(self) -> bool:
        return self.status in ("approved", "rejected", "cancelled")


# ─────────────────────────────────────────────────────────────────────────────
#  Built-in approval chains
# ─────────────────────────────────────────────────────────────────────────────

APPROVAL_CHAINS: list[ApprovalChain] = [
    ApprovalChain(
        id="prod_deploy",
        name="Production Deployment",
        trigger_pattern="production deploy",
        required_roles=["qa_lead", "sre_lead", "security_engineer"],
        strategy="all",
        timeout_hours=4.0,
        escalation_path=["CTO"],
        requires_human=True,
        risk_threshold="high",
        description="All production deployments require QA + SRE + Security sign-off",
    ),
    ApprovalChain(
        id="marketing_campaign",
        name="Marketing Campaign Launch",
        trigger_pattern="marketing campaign launch",
        required_roles=["legal_specialist", "marketing_manager", "CMO"],
        strategy="all",
        timeout_hours=8.0,
        escalation_path=["CMO"],
        requires_human=True,
        risk_threshold="medium",
        description="Campaign launch requires legal, brand, and CMO approval",
    ),
    ApprovalChain(
        id="financial_50k",
        name="Financial Commitment > $50k",
        trigger_pattern="financial commitment",
        required_roles=["CFO", "financial_controller"],
        strategy="all",
        timeout_hours=24.0,
        escalation_path=["CEO"],
        requires_human=True,
        risk_threshold="high",
        description="Large financial commitments require CFO and controller sign-off",
    ),
    ApprovalChain(
        id="external_data_sharing",
        name="External Data Sharing",
        trigger_pattern="external data sharing",
        required_roles=["compliance_officer", "legal_specialist", "security_engineer"],
        strategy="all",
        timeout_hours=12.0,
        escalation_path=["CTO", "legal_counsel"],
        requires_human=True,
        risk_threshold="high",
        description="Sharing data externally requires privacy, legal, and security approval",
    ),
    ApprovalChain(
        id="legal_agreement",
        name="Legal Agreement",
        trigger_pattern="legal agreement",
        required_roles=["legal_counsel", "legal_specialist"],
        strategy="any",
        timeout_hours=48.0,
        escalation_path=["CEO"],
        requires_human=True,
        risk_threshold="critical",
        description="Binding legal agreements require legal review and human sign-off",
    ),
    ApprovalChain(
        id="email_campaign_mass",
        name="Mass Email Campaign",
        trigger_pattern="email campaign mass send",
        required_roles=["legal_specialist", "marketing_manager"],
        strategy="all",
        timeout_hours=4.0,
        escalation_path=["CMO"],
        requires_human=False,
        risk_threshold="medium",
        description="Mass email requires legal and marketing lead approval",
    ),
    ApprovalChain(
        id="infrastructure_change",
        name="Infrastructure Change",
        trigger_pattern="infrastructure modify",
        required_roles=["devops_engineer", "security_engineer"],
        strategy="all",
        timeout_hours=4.0,
        escalation_path=["CTO"],
        requires_human=True,
        risk_threshold="high",
        description="Infrastructure changes need DevOps and Security review",
    ),
    ApprovalChain(
        id="pii_bulk_operation",
        name="PII Bulk Operation",
        trigger_pattern="pii bulk",
        required_roles=["compliance_officer", "data_governance_officer"],
        strategy="all",
        timeout_hours=12.0,
        escalation_path=["CTO", "legal_counsel"],
        requires_human=True,
        risk_threshold="critical",
        description="Bulk PII operations always require human approval",
    ),
    ApprovalChain(
        id="public_communication",
        name="Public Communication",
        trigger_pattern="public statement press release",
        required_roles=["CMO", "legal_specialist"],
        strategy="all",
        timeout_hours=8.0,
        escalation_path=["CEO"],
        requires_human=True,
        risk_threshold="high",
        description="Press releases and public statements require CMO + Legal + CEO",
    ),
    ApprovalChain(
        id="budget_override",
        name="Budget Override",
        trigger_pattern="budget override exceed",
        required_roles=["CFO"],
        strategy="any",
        timeout_hours=6.0,
        escalation_path=["CEO"],
        requires_human=True,
        risk_threshold="medium",
        description="Exceeding budget limits requires CFO approval",
    ),
    ApprovalChain(
        id="security_critical",
        name="Critical Security Action",
        trigger_pattern="security critical",
        required_roles=["security_engineer", "CTO"],
        strategy="all",
        timeout_hours=2.0,
        escalation_path=["CEO"],
        requires_human=True,
        risk_threshold="critical",
        description="Critical security actions require immediate security team + CTO approval",
    ),
    ApprovalChain(
        id="compliance_critical",
        name="Compliance-Critical Action",
        trigger_pattern="compliance violation",
        required_roles=["compliance_officer", "legal_specialist"],
        strategy="all",
        timeout_hours=4.0,
        escalation_path=["CEO", "legal_counsel"],
        requires_human=True,
        risk_threshold="critical",
        description="Compliance-critical actions stop the mission until resolved",
    ),
]

# Index by ID for fast lookup
CHAINS_BY_ID: dict[str, ApprovalChain] = {c.id: c for c in APPROVAL_CHAINS}

# Risk level ordering
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ─────────────────────────────────────────────────────────────────────────────
#  Engine
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalChainEngine:
    """Runtime engine for approval chain matching and request management."""

    # In-memory store for tests / demo; production wires a DB-backed store
    def __init__(self, store: dict[str, ApprovalRequest] | None = None) -> None:
        self._store: dict[str, ApprovalRequest] = store if store is not None else {}

    async def check_requires_approval(
        self,
        action: str,
        context: dict[str, Any],
        org: Any,
    ) -> ApprovalChain | None:
        with _tracer.start_as_current_span("approval_chain.check") as span:
            span.set_attribute("action", action)
            action_lower = action.lower()
            risk = str(getattr(org, "risk_tolerance", "medium") or "medium")
            org_autonomy = int(getattr(org, "autonomy_level", 3) or 3)

            for chain in APPROVAL_CHAINS:
                pattern_words = chain.trigger_pattern.lower().split()
                if all(w in action_lower for w in pattern_words):
                    # Check risk threshold
                    if _RISK_ORDER.get(chain.risk_threshold, 0) <= _RISK_ORDER.get(risk, 1):
                        # Check autonomy level
                        if org_autonomy <= 4:
                            span.set_attribute("matched_chain", chain.id)
                            _log.info(
                                "approval_chain.matched",
                                action=action,
                                chain=chain.id,
                            )
                            return chain

            span.set_attribute("matched_chain", "none")
            return None

    async def create_approval_request(
        self,
        chain: ApprovalChain,
        action_detail: str,
        mission_id: str | None,
        agent_id: str | None,
        tenant_id: str,
        org_id: str | None = None,
    ) -> ApprovalRequest:
        with _tracer.start_as_current_span("approval_chain.create_request") as span:
            now = datetime.now(UTC)
            req = ApprovalRequest(
                request_id=str(uuid.uuid4()),
                chain_id=chain.id,
                action=chain.name,
                action_detail=action_detail,
                mission_id=mission_id,
                agent_id=agent_id,
                tenant_id=tenant_id,
                org_id=org_id,
                approvers_needed=list(chain.required_roles),
                created_at=now,
                expires_at=now + timedelta(hours=chain.timeout_hours),
                status="pending",
            )
            self._store[req.request_id] = req
            span.set_attribute("request_id", req.request_id)
            span.set_attribute("chain_id", chain.id)
            _log.info(
                "approval_chain.request_created",
                request_id=req.request_id,
                chain=chain.id,
                action=action_detail[:60],
            )
            return req

    async def record_approval(
        self, request_id: str, approver_role: str, approved: bool, notes: str = ""
    ) -> ApprovalRequest:
        with _tracer.start_as_current_span("approval_chain.record") as span:
            req = self._store.get(request_id)
            if req is None:
                raise KeyError(f"Approval request {request_id!r} not found")
            if req.is_resolved:
                return req

            req.approvers_responded.append(approver_role)
            if approved:
                req.approved_by.append(approver_role)
            else:
                req.rejected_by.append(approver_role)
                req.notes = notes
                req.status = "rejected"
                req.resolved_at = datetime.now(UTC)
                span.set_attribute("outcome", "rejected")
                return req

            chain = CHAINS_BY_ID.get(req.chain_id)
            if chain and await self.check_approval_complete(request_id, req.approved_by):
                req.status = "approved"
                req.resolved_at = datetime.now(UTC)
                span.set_attribute("outcome", "approved")
            return req

    async def check_approval_complete(
        self, request_id: str, approvals: list[str],
    ) -> bool:
        req = self._store.get(request_id)
        if req is None:
            return False
        chain = CHAINS_BY_ID.get(req.chain_id)
        if chain is None:
            return bool(approvals)

        needed = set(chain.required_roles)
        given = set(approvals)
        if chain.strategy == "all":
            return needed.issubset(given)
        if chain.strategy == "any":
            return bool(needed & given)
        if chain.strategy == "majority":
            return len(needed & given) >= len(needed) // 2 + 1
        return bool(approvals)

    async def escalate_timeout(self, request_id: str) -> None:
        with _tracer.start_as_current_span("approval_chain.escalate"):
            req = self._store.get(request_id)
            if req is None or req.is_resolved:
                return
            if not req.is_expired:
                return
            req.status = "escalated"
            req.escalated_at = datetime.now(UTC)
            chain = CHAINS_BY_ID.get(req.chain_id)
            escalation_targets = chain.escalation_path if chain else []
            _log.warning(
                "approval_chain.timeout_escalated",
                request_id=request_id,
                chain=req.chain_id,
                escalation_targets=escalation_targets,
            )

    async def get_request(self, request_id: str) -> ApprovalRequest | None:
        return self._store.get(request_id)

    async def list_pending(
        self, tenant_id: str, org_id: str | None = None,
    ) -> list[ApprovalRequest]:
        return [
            r for r in self._store.values()
            if r.tenant_id == tenant_id
            and r.status == "pending"
            and (org_id is None or r.org_id == org_id)
        ]


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton factory
# ─────────────────────────────────────────────────────────────────────────────
_engine: ApprovalChainEngine | None = None


def get_approval_engine() -> ApprovalChainEngine:
    """Return the process-level ApprovalChainEngine singleton."""
    global _engine
    if _engine is None:
        _engine = ApprovalChainEngine()
    return _engine
