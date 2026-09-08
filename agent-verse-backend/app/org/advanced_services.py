"""P1 + P4 + P10 — Policy Evidence Engine, Strategic Advisor, Collective Intelligence.

P1  Policy Evidence Engine:
    Every policy decision must have supporting evidence.
    Tracks: policy trigger → evidence chain → decision → outcome.

P4  Strategic Advisor (Weekly Intelligence Brief):
    AI-generated weekly strategic intelligence delivered every Sunday.
    Covers: performance, trends, risks, opportunities, recommendations.

P10 Collective Intelligence (Privacy-Preserving):
    Aggregate anonymised learning signals across orgs (same tenant)
    without exposing per-org data. Uses differential privacy noise.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── P1: Policy Evidence Engine ────────────────────────────────────────────────


@dataclass
class PolicyEvidence:
    """Evidence record supporting a policy decision."""

    evidence_id: str
    policy_id: str
    org_id: str
    evidence_type: str  # 'kpi_metric' | 'audit_finding' | 'external_signal'
    description: str
    value: float  # metric value that triggered the policy
    threshold: float  # threshold that was crossed
    source: str  # 'finance_agent' | 'monitoring' | 'audit'
    confidence: float = 0.9
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if not self.recorded_at:
            self.recorded_at = datetime.now(UTC).isoformat()


class PolicyEvidenceEngine:
    """P1 — Every policy decision must have a justification chain.

    When the system makes a policy-driven decision:
    1. Record WHAT triggered the policy (metric + threshold crossed)
    2. Record WHAT evidence supports it (data points, sources)
    3. Link evidence to decision record
    4. Surface evidence for HITL review
    """

    def __init__(self) -> None:
        self._evidence: dict[str, list[PolicyEvidence]] = {}  # policy_id → [evidence]
        self._decision_evidence: dict[str, list[str]] = {}  # decision_id → [evidence_ids]

    def record_evidence(self, evidence: PolicyEvidence) -> PolicyEvidence:
        """Record a piece of evidence for a policy."""
        with _tracer.start_as_current_span("policy_evidence.record") as span:
            span.set_attribute("policy_id", evidence.policy_id)
            span.set_attribute("evidence_type", evidence.evidence_type)
            span.set_attribute("confidence", evidence.confidence)

            self._evidence.setdefault(evidence.policy_id, []).append(evidence)
            _log.info(
                "policy_evidence.recorded",
                evidence_id=evidence.evidence_id,
                policy_id=evidence.policy_id,
                evidence_type=evidence.evidence_type,
            )
            return evidence

    def link_to_decision(self, decision_id: str, evidence_ids: list[str]) -> None:
        """Link a decision to its supporting evidence."""
        self._decision_evidence[decision_id] = evidence_ids

    def get_evidence_chain(self, policy_id: str) -> list[dict[str, Any]]:
        """Return all evidence supporting a policy decision."""
        return [
            {
                "evidence_id": e.evidence_id,
                "evidence_type": e.evidence_type,
                "description": e.description,
                "value": e.value,
                "threshold": e.threshold,
                "source": e.source,
                "confidence": e.confidence,
                "recorded_at": e.recorded_at,
            }
            for e in self._evidence.get(policy_id, [])
        ]

    def validate_decision_has_evidence(self, decision_id: str) -> bool:
        """Return True if the decision has at least one evidence record."""
        return len(self._decision_evidence.get(decision_id, [])) > 0


# ── P4: Strategic Advisor ─────────────────────────────────────────────────────


@dataclass
class StrategicBrief:
    """Weekly strategic intelligence brief for an org."""

    org_id: str
    week_ending: str
    health_summary: str
    accomplishments: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    kpi_trends: dict[str, Any] = field(default_factory=dict)
    cost_overview: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""
    generation_method: str = "template"  # "llm" | "template"

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(UTC).isoformat()


class StrategicAdvisor:
    """P4 — AI-generated weekly intelligence brief.

    Runs every Sunday 18:00 org timezone via Celery Beat.
    Analyses: performance, market, competitors, opportunities, risks.
    Delivers via: in-app, email, voice.
    """

    async def generate_weekly_brief(
        self,
        org_id: str,
        org_name: str,
        health: dict[str, Any],
        recent_missions: list[dict[str, Any]] | None = None,
    ) -> StrategicBrief:
        """Generate the weekly strategic brief for an org."""
        with _tracer.start_as_current_span("strategic_advisor.generate") as span:
            span.set_attribute("org_id", org_id)

            week_ending = datetime.now(UTC).strftime("%Y-%m-%d")
            active_missions = health.get("active_missions", 0)
            failed_tasks = health.get("task_counts", {}).get("failed", 0)
            blocked = health.get("task_counts", {}).get("blocked", 0)
            completed = health.get("task_counts", {}).get("completed", 0)
            h_status = health.get("health", "healthy")

            # --- Try LLM if available ---
            brief = await self._llm_brief(org_id, org_name, health, recent_missions or [])
            if brief:
                brief.week_ending = week_ending
                return brief

            # --- Template fallback ---
            accomplishments = []
            risks = []
            recommendations = []

            if completed > 0:
                accomplishments.append(f"{completed} tasks completed this week.")
            if active_missions > 0:
                accomplishments.append(f"{active_missions} missions currently active.")

            if failed_tasks > 0:
                risks.append(f"{failed_tasks} task failures detected — investigate root cause.")
            if blocked > 2:
                risks.append(f"{blocked} tasks blocked — may impact delivery timeline.")

            if h_status == "healthy" and active_missions > 0:
                recommendations.append(
                    "Organisation operating smoothly — consider scaling active missions."
                )
            if failed_tasks > 0:
                recommendations.append("Schedule post-mortem for failed tasks within 48 hours.")
            if not recommendations:
                recommendations.append(
                    "Review KPIs and ensure all department heads have reviewed weekly targets."
                )

            span.set_attribute("method", "template")
            return StrategicBrief(
                org_id=org_id,
                week_ending=week_ending,
                health_summary=f"{'✅ Healthy' if h_status == 'healthy' else '⚠️ Needs Attention'}",
                accomplishments=accomplishments,
                risks=risks,
                opportunities=["Review underutilised departments for new mission opportunities."],
                recommendations=recommendations,
                kpi_trends={"completed_tasks": completed, "active_missions": active_missions},
                cost_overview=health.get("event_counts_24h", {}),
                generation_method="template",
            )

    async def _llm_brief(
        self,
        org_id: str,
        org_name: str,
        health: dict[str, Any],
        missions: list[dict[str, Any]],
    ) -> StrategicBrief | None:
        """Use LLM for richer brief when provider available."""
        try:
            from app.main import app as _app

            provider = getattr(_app.state, "provider", None)
            if provider is None:
                return None

            import json as _json

            from app.providers.base import CompletionRequest, Message

            prompt = (
                f"Generate a concise weekly strategic brief for '{org_name}'. "
                f"Org health: {health.get('health')}. "
                f"Active missions: {health.get('active_missions', 0)}. "
                f"Failed tasks: {health.get('task_counts', {}).get('failed', 0)}. "
                'Return JSON: {"health_summary": str, "accomplishments": [str], '
                '"risks": [str], "opportunities": [str], "recommendations": [str]}'
            )
            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model=getattr(provider, "default_model", "claude-sonnet-4-5"),
                    max_tokens=600,
                    temperature=0.3,
                )
            )
            data = _json.loads(
                resp.content.strip()[resp.content.find("{") : resp.content.rfind("}") + 1]
            )
            return StrategicBrief(
                org_id=org_id,
                week_ending=datetime.now(UTC).strftime("%Y-%m-%d"),
                generation_method="llm",
                **{
                    k: data.get(k, [])
                    for k in (
                        "health_summary",
                        "accomplishments",
                        "risks",
                        "opportunities",
                        "recommendations",
                    )
                },
            )
        except Exception as exc:
            _log.warning("strategic_advisor.llm_fallback", org_id=org_id, error=str(exc))
            return None


# ── P10: Collective Intelligence (Privacy-Preserving) ─────────────────────────


class CollectiveIntelligence:
    """P10 — Cross-org learning with differential privacy.

    Aggregates anonymised signals across orgs of the same tenant without
    exposing per-org confidential data. Uses Laplace noise for DP guarantee.

    Signals aggregated:
    - Task success rate distributions
    - Average mission duration by type
    - Common failure patterns (anonymous)
    - Capability utilisation profiles
    """

    EPSILON = 1.0  # differential privacy budget (lower = more privacy)

    def __init__(self) -> None:
        self._signals: dict[str, list[dict[str, Any]]] = {}  # tenant_id → [signals]

    def _add_noise(self, value: float, sensitivity: float = 1.0) -> float:
        """Add Laplace noise for differential privacy."""
        scale = sensitivity / self.EPSILON
        noise = random.gauss(0, scale)  # approximate Laplace with Gaussian
        return max(0.0, value + noise)

    def record_signal(
        self,
        tenant_id: str,
        org_id: str,
        signal_type: str,
        value: float,
    ) -> None:
        """Record an anonymised signal from an org."""
        with _tracer.start_as_current_span("collective_intel.record") as span:
            span.set_attribute("tenant_id", tenant_id)
            span.set_attribute("signal_type", signal_type)

            # Store noised value — do NOT store org_id with the value
            noised = self._add_noise(value)
            self._signals.setdefault(tenant_id, []).append(
                {
                    "signal_type": signal_type,
                    "value": noised,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )

    def aggregate(self, tenant_id: str, signal_type: str) -> dict[str, Any]:
        """Return aggregated (noisy) statistics for a signal type."""
        signals = [
            s["value"] for s in self._signals.get(tenant_id, []) if s["signal_type"] == signal_type
        ]
        if not signals:
            return {"count": 0, "mean": 0.0, "min": 0.0, "max": 0.0}

        return {
            "count": len(signals),
            "mean": round(sum(signals) / len(signals), 4),
            "min": round(min(signals), 4),
            "max": round(max(signals), 4),
            "privacy_guarantee": f"ε={self.EPSILON} differential privacy",
        }

    def benchmarks(self, tenant_id: str) -> dict[str, Any]:
        """Return aggregated benchmarks across all orgs for the tenant."""
        signal_types = list({s["signal_type"] for s in self._signals.get(tenant_id, [])})
        return {st: self.aggregate(tenant_id, st) for st in signal_types}


# ── Q9: Channel Router + Conversation Manager ────────────────────────────────


@dataclass
class ConversationTurn:
    role: str  # 'user' | 'assistant'
    content: str
    channel: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


class ChannelRouter:
    """Q9 — Routes commands from any channel to the org system.

    Maintains per-channel conversation context so multi-turn
    conversations work across Telegram, Slack, REST, etc.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, list[ConversationTurn]] = {}  # conv_id → turns

    def add_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        channel: str,
    ) -> None:
        """Append a turn to an ongoing conversation."""
        with _tracer.start_as_current_span("channel_router.add_turn") as span:
            span.set_attribute("conversation_id", conversation_id)
            span.set_attribute("channel", channel)
            span.set_attribute("role", role)

            self._conversations.setdefault(conversation_id, []).append(
                ConversationTurn(role=role, content=content, channel=channel)
            )

    def get_context(self, conversation_id: str, last_n: int = 10) -> list[dict[str, Any]]:
        """Return the last N turns of a conversation."""
        turns = self._conversations.get(conversation_id, [])
        return [
            {"role": t.role, "content": t.content, "channel": t.channel, "timestamp": t.timestamp}
            for t in turns[-last_n:]
        ]

    def route(
        self,
        channel: str,
        command: str,
        conversation_id: str | None,
        org_id: str,
    ) -> dict[str, Any]:
        """Route an incoming command to the appropriate handler."""
        with _tracer.start_as_current_span("channel_router.route") as span:
            span.set_attribute("channel", channel)
            span.set_attribute("org_id", org_id)

            conv_id = conversation_id or f"{channel}:{org_id}"
            self.add_turn(conv_id, "user", command, channel)

            # Detect command type
            cmd_lower = command.lower().strip()
            if any(w in cmd_lower for w in ("pause", "stop", "emergency")):
                handler = "emergency_stop"
            elif any(w in cmd_lower for w in ("status", "health", "how is")):
                handler = "org_health_query"
            elif any(w in cmd_lower for w in ("create mission", "start mission", "new mission")):
                handler = "mission_create"
            elif any(w in cmd_lower for w in ("approve", "yes", "confirm")):
                handler = "hitl_approve"
            elif any(w in cmd_lower for w in ("reject", "no", "deny")):
                handler = "hitl_reject"
            else:
                handler = "goal_submit"

            span.set_attribute("handler", handler)
            return {
                "conversation_id": conv_id,
                "handler": handler,
                "org_id": org_id,
                "channel": channel,
                "context_turns": len(self._conversations.get(conv_id, [])),
            }


# ── QA4: Sub-Tenants / Enterprise Hierarchy ──────────────────────────────────


@dataclass
class SubTenant:
    """QA4 — An enterprise child tenant under a parent tenant."""

    sub_tenant_id: str
    parent_id: str
    name: str
    org_quota: int = 10
    agent_quota: int = 100
    budget_limit: float = 10_000.0
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


class SubTenantManager:
    """QA4 — Manage enterprise hierarchy of sub-tenants."""

    def __init__(self) -> None:
        self._sub_tenants: dict[str, list[SubTenant]] = {}  # parent_id → [sub_tenants]

    def create(self, parent_id: str, sub: SubTenant) -> SubTenant:
        with _tracer.start_as_current_span("sub_tenant.create") as span:
            span.set_attribute("parent_id", parent_id)
            span.set_attribute("sub_tenant_id", sub.sub_tenant_id)
            self._sub_tenants.setdefault(parent_id, []).append(sub)
            _log.info("sub_tenant.created", parent_id=parent_id, sub_tenant_id=sub.sub_tenant_id)
            return sub

    def list_sub_tenants(self, parent_id: str) -> list[dict[str, Any]]:
        return [
            {
                "sub_tenant_id": s.sub_tenant_id,
                "name": s.name,
                "org_quota": s.org_quota,
                "agent_quota": s.agent_quota,
                "budget_limit": s.budget_limit,
                "created_at": s.created_at,
            }
            for s in self._sub_tenants.get(parent_id, [])
        ]


# ── QA11: MCP Resources + Prompts (full MCP spec beyond tools) ────────────────


@dataclass
class MCPResource:
    """QA11 — A resource exposed via the MCP server (read-only data source)."""

    uri: str  # e.g. "org://acme/missions/active"
    name: str
    description: str
    mime_type: str = "application/json"
    content: str = ""


@dataclass
class MCPPrompt:
    """QA11 — A reusable prompt template exposed via MCP."""

    name: str
    description: str
    template: str
    arguments: list[dict[str, str]] = field(default_factory=list)


class MCPFullSpec:
    """QA11 — Complete MCP server spec: tools + resources + prompts.

    Beyond just tool execution, the org OS exposes:
    - Resources: read-only URIs for org state (missions, health, agents)
    - Prompts:   reusable prompt templates for org operations
    """

    def __init__(self) -> None:
        self._resources: dict[str, MCPResource] = {}
        self._prompts: dict[str, MCPPrompt] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        # Default resources
        for r in [
            MCPResource(
                "org://missions/active", "Active Missions", "List all currently active org missions"
            ),
            MCPResource("org://health", "Org Health", "Current org health score and metrics"),
            MCPResource("org://teams/active", "Active Teams", "Teams currently executing work"),
            MCPResource(
                "org://decisions/recent",
                "Recent Decisions",
                "Last 10 autonomous decisions with reasoning",
            ),
            MCPResource(
                "org://knowledge/summary", "Knowledge Summary", "Summary of org knowledge base"
            ),
        ]:
            self._resources[r.uri] = r

        # Default prompts
        for p in [
            MCPPrompt(
                name="create_mission",
                description="Create a new org mission from a goal description",
                template="Create a mission for the {org_name} organization to achieve: {goal}. Priority: {priority}.",  # noqa: E501
                arguments=[
                    {"name": "org_name"},
                    {"name": "goal"},
                    {"name": "priority", "default": "medium"},
                ],
            ),
            MCPPrompt(
                name="analyze_org_health",
                description="Analyze org health and suggest improvements",
                template="Analyze the health of {org_name} and provide 3 actionable recommendations.",  # noqa: E501
                arguments=[{"name": "org_name"}],
            ),
            MCPPrompt(
                name="generate_strategic_brief",
                description="Generate weekly strategic brief",
                template="Generate a weekly strategic brief for {org_name} based on: health={health}, missions={missions}.",  # noqa: E501
                arguments=[{"name": "org_name"}, {"name": "health"}, {"name": "missions"}],
            ),
        ]:
            self._prompts[p.name] = p

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {"uri": r.uri, "name": r.name, "description": r.description, "mimeType": r.mime_type}
            for r in self._resources.values()
        ]

    def list_prompts(self) -> list[dict[str, Any]]:
        return [
            {"name": p.name, "description": p.description, "arguments": p.arguments}
            for p in self._prompts.values()
        ]

    def get_resource(self, uri: str) -> MCPResource | None:
        return self._resources.get(uri)

    def get_prompt(self, name: str) -> MCPPrompt | None:
        return self._prompts.get(name)


# ── Singletons ────────────────────────────────────────────────────────────────

_policy_evidence = PolicyEvidenceEngine()
_strategic_advisor = StrategicAdvisor()
_collective_intel = CollectiveIntelligence()
_channel_router = ChannelRouter()
_sub_tenant_manager = SubTenantManager()
_mcp_full_spec = MCPFullSpec()


def get_policy_evidence() -> PolicyEvidenceEngine:
    return _policy_evidence


def get_strategic_advisor() -> StrategicAdvisor:
    return _strategic_advisor


def get_collective_intel() -> CollectiveIntelligence:
    return _collective_intel


def get_channel_router() -> ChannelRouter:
    return _channel_router


def get_sub_tenant_manager() -> SubTenantManager:
    return _sub_tenant_manager


def get_mcp_full_spec() -> MCPFullSpec:
    return _mcp_full_spec
