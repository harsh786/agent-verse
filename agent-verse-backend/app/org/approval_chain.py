"""Cross-department approval chain engine.

Defines approval chains for high-risk actions (e.g. prod deployment, large spend)
and provides runtime methods to create/check/escalate approval requests.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
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
    Redis client is wired via ``set_redis()``; Redis is then the only source of
    truth (errors raise, no process-local fallback) and listing reads a bounded
    per-tenant index (a08-F178-02). Without Redis (tests) requests live in the
    in-memory ``store``.
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

    # a08-F178-02: with Redis wired, Redis is the only source of truth. Errors
    # raise (no per-process fallback that other replicas cannot see), records
    # carry a TTL, and listing reads a bounded per-tenant index instead of a
    # fleet-wide ``KEYS approval_chain:req:*`` scan.
    _INDEX_PREFIX = "approval_chain:idx:"
    # Resolved/expired requests stay readable this long past their expiry.
    _RETENTION = timedelta(days=7)
    # Upper bound on one tenant's listing (newest first).
    LIST_LIMIT = 500

    def _index_key(self, tenant_id: str) -> str:
        return f"{self._INDEX_PREFIX}{tenant_id}"

    async def _persist(self, req: ApprovalRequest) -> None:
        if self._redis is None:
            return
        now = datetime.now(UTC)
        ttl = max(int((req.expires_at - now + self._RETENTION).total_seconds()), 60)
        key = f"{self._REDIS_PREFIX}{req.request_id}"
        idx = self._index_key(req.tenant_id)
        await self._redis.set(key, self._serialize(req), ex=ttl)
        await self._redis.zadd(idx, {req.request_id: req.created_at.timestamp()})
        # The index outlives its newest member by the same retention; members
        # older than that are trimmed so the set stays bounded.
        await self._redis.zremrangebyscore(
            idx, "-inf", (now - self._RETENTION - timedelta(days=30)).timestamp()
        )
        await self._redis.expire(idx, ttl)

    async def _load(self, request_id: str) -> ApprovalRequest | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(f"{self._REDIS_PREFIX}{request_id}")
        if raw is None:
            return None
        return self._deserialize(raw)

    async def _get(self, request_id: str) -> ApprovalRequest | None:
        """Current state: Redis when wired (never a stale local copy), else memory."""
        if self._redis is not None:
            return await self._load(request_id)
        return self._store.get(request_id)

    async def _load_tenant(self, redis: Any, tenant_id: str, limit: int) -> list[ApprovalRequest]:
        idx = self._index_key(tenant_id)
        raw_ids = await redis.zrevrange(idx, 0, max(limit, 1) - 1)
        if not raw_ids:
            return []
        ids = [i.decode() if isinstance(i, bytes) else str(i) for i in raw_ids]
        raws = await redis.mget([f"{self._REDIS_PREFIX}{i}" for i in ids])
        out: list[ApprovalRequest] = []
        gone: list[str] = []
        for rid, raw in zip(ids, raws, strict=True):
            if raw is None:
                gone.append(rid)
                continue
            req = self._deserialize(raw)
            if req.tenant_id == tenant_id:
                out.append(req)
        if gone:
            await redis.zrem(idx, *gone)
        return out

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
            if self._redis is None:
                self._store[req.request_id] = req
            else:
                # Raises on a Redis error: a request other replicas cannot see
                # is not created (a08-F178-02).
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

            def _apply(req: ApprovalRequest) -> bool:
                if req.is_resolved:
                    return False
                req.approvers_responded.append(approver_role)
                if approved:
                    req.approved_by.append(approver_role)
                    if self._complete(req, req.approved_by):
                        req.status = "approved"
                        req.resolved_at = datetime.now(UTC)
                        span.set_attribute("outcome", "approved")
                else:
                    req.rejected_by.append(approver_role)
                    req.notes = notes
                    req.status = "rejected"
                    req.resolved_at = datetime.now(UTC)
                    span.set_attribute("outcome", "rejected")
                return True

            req, _changed = await self._mutate(request_id, _apply)
            return req

    async def _mutate(
        self, request_id: str, apply: Callable[[ApprovalRequest], bool]
    ) -> tuple[ApprovalRequest, bool]:
        """Read-modify-write one request atomically across replicas.

        With Redis wired this is a WATCH/MULTI optimistic transaction (retried
        on a concurrent write), so two approvers on different replicas cannot
        overwrite each other's vote. ``apply`` returns False to skip the write;
        the result says whether this call's write landed. Missing → KeyError.
        """
        if self._redis is None:
            req = self._store.get(request_id)
            if req is None:
                raise KeyError(f"Approval request {request_id!r} not found")
            return req, apply(req)
        from redis.exceptions import WatchError

        key = f"{self._REDIS_PREFIX}{request_id}"
        for _ in range(10):
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        raise KeyError(f"Approval request {request_id!r} not found")
                    req = self._deserialize(raw)
                    if not apply(req):
                        return req, False
                    ttl = await pipe.ttl(key)
                    pipe.multi()
                    pipe.set(key, self._serialize(req), ex=ttl if ttl and ttl > 0 else 60)
                    await pipe.execute()
                    return req, True
                except WatchError:
                    continue
        raise RuntimeError(f"approval request {request_id!r}: too much write contention")

    async def check_approval_complete(
        self,
        request_id: str,
        approvals: list[str],
    ) -> bool:
        req = await self._get(request_id)
        if req is None:
            return False
        return self._complete(req, approvals)

    @staticmethod
    def _complete(req: ApprovalRequest, approvals: list[str]) -> bool:
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

            def _apply(r: ApprovalRequest) -> bool:
                if r.is_resolved or not r.is_expired or r.status == "escalated":
                    return False
                r.status = "escalated"
                r.escalated_at = datetime.now(UTC)
                return True

            try:
                req, changed = await self._mutate(request_id, _apply)
            except KeyError:
                return
            # Only the replica whose write flipped it to escalated acts on it.
            if not changed:
                return
            chain = CHAINS_BY_ID.get(req.chain_id)
            escalation_targets = chain.escalation_path if chain else []
            span.set_attribute("escalation_targets", ",".join(escalation_targets))
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
        return await self._get(request_id)

    async def list_pending(
        self,
        tenant_id: str,
        org_id: str | None = None,
        limit: int = LIST_LIMIT,
    ) -> list[ApprovalRequest]:
        # a08-F178-02: one tenant's bounded index, newest first; never a KEYS scan.
        if self._redis is not None:
            all_reqs = await self._load_tenant(self._redis, tenant_id, limit)
        else:
            all_reqs = list(self._store.values())
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
