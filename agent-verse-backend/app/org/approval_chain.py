"""Cross-department approval chain engine.

Defines approval chains for high-risk actions (e.g. prod deployment, large spend)
and provides runtime methods to create/check/escalate approval requests.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
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
    trigger_pattern: str  # keyword pattern or exact action name
    required_roles: list[str]  # role names or dept_kind:role patterns
    strategy: str  # "all" | "any" | "majority"
    timeout_hours: float
    escalation_path: list[str]  # roles to escalate to on timeout
    requires_human: bool
    risk_threshold: str  # minimum risk level: "low"|"medium"|"high"|"critical"
    description: str = ""

    # Convenience aliases used by ApprovalChainRegistry consumers
    @property
    def required_approvers(self) -> list[str]:
        """Alias for required_roles — preferred public surface."""
        return self.required_roles

    @property
    def any_or_all(self) -> str:
        """Return 'all' or 'any' normalised from strategy."""
        if self.strategy == "all":
            return "all"
        if self.strategy == "any":
            return "any"
        return "all"  # majority treated as all for binary UI


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
    approvers_needed: list[str]  # role names required
    approvers_responded: list[str] = field(default_factory=list)
    approved_by: list[str] = field(default_factory=list)
    rejected_by: list[str] = field(default_factory=list)
    status: str = "pending"  # pending|approved|rejected|escalated|expired|cancelled
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
    """Runtime engine for approval chain matching and request management.

    G-20: Requests are persisted in Redis (cross-replica, restart-safe) when a
    Redis client is wired via ``set_redis()``. Tests can pass an explicit in-memory
    ``store`` to operate entirely without Redis.
    """

    _REDIS_PREFIX = "approval_chain:req:"

    def __init__(self, store: dict[str, ApprovalRequest] | None = None) -> None:
        self._store: dict[str, ApprovalRequest] = store if store is not None else {}
        # G-20: optional Redis client for cross-replica persistence.
        self._redis: Any | None = None

    # G-20: wire a Redis client to enable persistence across replicas + restarts.
    def set_redis(self, redis_client: Any | None) -> None:
        self._redis = redis_client

    # ── G-20 persistence helpers ─────────────────────────────────────────────
    def _serialize(self, req: ApprovalRequest) -> str:
        data = asdict(req)
        for _k, _v in list(data.items()):
            if isinstance(_v, datetime):
                data[_k] = _v.isoformat()
        return json.dumps(data, default=str)

    def _deserialize(self, raw: str | bytes) -> ApprovalRequest:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        for _k in ("created_at", "expires_at", "resolved_at", "escalated_at"):
            _v = data.get(_k)
            if isinstance(_v, str) and _v:
                try:
                    data[_k] = datetime.fromisoformat(_v)
                except ValueError:
                    data[_k] = None
        allowed = {k: v for k, v in data.items() if k in ApprovalRequest.__dataclass_fields__}
        return ApprovalRequest(**allowed)

    async def _persist(self, req: ApprovalRequest) -> None:
        if self._redis is None:
            return
        try:
            key = f"{self._REDIS_PREFIX}{req.request_id}"
            await self._redis.set(key, self._serialize(req))
        except Exception as exc:
            _log.warning("approval_chain.persist_failed", request_id=req.request_id, error=str(exc))

    async def _load(self, request_id: str) -> ApprovalRequest | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"{self._REDIS_PREFIX}{request_id}")
            if raw is None:
                return None
            return self._deserialize(raw)
        except Exception as exc:
            _log.warning("approval_chain.load_failed", request_id=request_id, error=str(exc))
            return None

    async def _load_all(self) -> list[ApprovalRequest]:
        if self._redis is None:
            return list(self._store.values())
        try:
            keys = await self._redis.keys(f"{self._REDIS_PREFIX}*")
            results: list[ApprovalRequest] = []
            for k in keys:
                raw = await self._redis.get(k)
                if raw:
                    results.append(self._deserialize(raw))
            return results
        except Exception as exc:
            _log.warning("approval_chain.load_all_failed", error=str(exc))
            return list(self._store.values())

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
                # Fire the chain when the action's risk is at least as high
                # as the org's tolerance for autonomous action — i.e. a
                # high-risk action is gated even for medium-tolerance orgs.
                # (Also requires autonomy level to be checked.)
                if (
                    all(w in action_lower for w in pattern_words)
                    and _RISK_ORDER.get(chain.risk_threshold, 0) >= _RISK_ORDER.get(risk, 0)
                    and org_autonomy <= 4
                ):
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
            await self._persist(req)
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
            if req is None and self._redis is not None:
                req = await self._load(request_id)
                if req is not None:
                    self._store[request_id] = req
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
                await self._persist(req)
                return req

            chain = CHAINS_BY_ID.get(req.chain_id)
            if chain and await self.check_approval_complete(request_id, req.approved_by):
                req.status = "approved"
                req.resolved_at = datetime.now(UTC)
                span.set_attribute("outcome", "approved")
            await self._persist(req)
            return req

    async def check_approval_complete(
        self,
        request_id: str,
        approvals: list[str],
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
        with _tracer.start_as_current_span("approval_chain.escalate") as span:
            req = self._store.get(request_id)
            if req is None and self._redis is not None:
                req = await self._load(request_id)
                if req is not None:
                    self._store[request_id] = req
            if req is None or req.is_resolved:
                return
            if not req.is_expired:
                return
            req.status = "escalated"
            req.escalated_at = datetime.now(UTC)
            chain = CHAINS_BY_ID.get(req.chain_id)
            escalation_targets = chain.escalation_path if chain else []
            span.set_attribute("escalation_targets", ",".join(escalation_targets))
            await self._persist(req)
            _log.warning(
                "approval_chain.timeout_escalated",
                request_id=request_id,
                chain=req.chain_id,
                escalation_targets=escalation_targets,
            )

            # G-26: Take real escalation actions — notify humans + publish event.
            try:
                from app.org.events import get_org_event_publisher

                _pub = get_org_event_publisher()
                if req.org_id:
                    await _pub.publish(
                        event_type="org.approval.timeout",
                        org_id=req.org_id,
                        tenant_id=req.tenant_id,
                        payload={
                            "request_id": request_id,
                            "chain_id": req.chain_id,
                            "mission_id": req.mission_id,
                            "escalation_targets": escalation_targets,
                        },
                    )
            except Exception as _ev_exc:
                _log.warning("approval_chain.escalate_event_failed", error=str(_ev_exc)[:100])

            # G-26: Notify the escalation roles via the notification service.
            try:
                from app.main import app as _app

                _notif = getattr(getattr(_app, "state", None), "notification_service", None)
                if _notif is not None and hasattr(_notif, "notify_approval_timeout"):
                    await _notif.notify_approval_timeout(
                        request_id=request_id,
                        goal_id=str(req.mission_id or ""),
                        action=req.action_detail or req.action,
                        tenant_id=req.tenant_id,
                    )
            except Exception as _n_exc:
                _log.warning("approval_chain.escalate_notify_failed", error=str(_n_exc)[:100])

            # G-26: Create a new approval request directed at the escalation path.
            if chain is not None and escalation_targets:
                try:
                    escalation_chain = ApprovalChain(
                        id=f"{chain.id}:escalation",
                        name=f"{chain.name} (Escalation)",
                        trigger_pattern=chain.trigger_pattern,
                        required_roles=escalation_targets,
                        strategy="any",
                        timeout_hours=max(chain.timeout_hours, 1.0),
                        escalation_path=[],
                        requires_human=True,
                        risk_threshold=chain.risk_threshold,
                        description=f"Escalation of timed-out request {request_id}",
                    )
                    await self.create_approval_request(
                        chain=escalation_chain,
                        action_detail=f"Escalation: {req.action_detail}",
                        mission_id=req.mission_id,
                        agent_id=req.agent_id,
                        tenant_id=req.tenant_id,
                        org_id=req.org_id,
                    )
                except Exception as _esc_create_exc:
                    _log.warning(
                        "approval_chain.escalation_request_create_failed",
                        error=str(_esc_create_exc)[:100],
                    )

    async def get_request(self, request_id: str) -> ApprovalRequest | None:
        req = self._store.get(request_id)
        if req is None and self._redis is not None:
            req = await self._load(request_id)
            if req is not None:
                self._store[request_id] = req
        return req

    async def list_pending(
        self,
        tenant_id: str,
        org_id: str | None = None,
    ) -> list[ApprovalRequest]:
        # G-20: merge in-memory + Redis-backed requests
        all_reqs: list[ApprovalRequest] = list(self._store.values())
        if self._redis is not None:
            redis_reqs = await self._load_all()
            seen_ids = {r.request_id for r in all_reqs}
            for r in redis_reqs:
                if r.request_id not in seen_ids:
                    all_reqs.append(r)
        return [
            r
            for r in all_reqs
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


# ─────────────────────────────────────────────────────────────────────────────
#  Registry — thin lookup wrapper around CHAINS_BY_ID
# ─────────────────────────────────────────────────────────────────────────────

# Human-friendly aliases so callers don't have to know the exact chain ID.
_CHAIN_ALIASES: dict[str, str] = {
    "financial_commitment_50k": "financial_50k",
    "financial_commitment": "financial_50k",
}


class ApprovalChainRegistry:
    """Read-only registry of built-in approval chains.

    Usage::

        reg = ApprovalChainRegistry()
        chain = reg.get("prod_deploy")   # None when not found
        chains = reg.list_all()
    """

    def get(self, chain_id: str) -> ApprovalChain | None:
        """Return the chain for *chain_id* (or a registered alias), or None."""
        resolved = _CHAIN_ALIASES.get(chain_id, chain_id)
        return CHAINS_BY_ID.get(resolved)

    def list_all(self) -> list[ApprovalChain]:
        """Return every built-in approval chain."""
        return list(APPROVAL_CHAINS)
