"""TeamFormationEngine — assembles an optimal team manifest from a mission goal.

Pipeline:
  goal_text → extract capabilities → map to roles → assign departments →
  estimate cost/duration → assess risk → compute success probability →
  produce TeamManifest
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog
from opentelemetry import trace

from app.org.capability_registry import (
    CAPABILITY_REGISTRY,
    DEPT_CAPABILITY_MAP,
    DEPT_LLM_PROFILES,
    get_model_profile_for_dept,
)

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Cost estimates (USD per agent-hour by seniority/type)
# ─────────────────────────────────────────────────────────────────────────────
COST_ESTIMATES: dict[str, float] = {
    "executive": 0.50,
    "senior_engineer": 0.30,
    "engineer": 0.20,
    "analyst": 0.15,
    "researcher": 0.15,
    "specialist": 0.25,
    "writer": 0.10,
    "support": 0.08,
    "worker": 0.05,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Role definitions
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RoleDefinition:
    department_kind: str
    seniority: str  # junior|mid|senior|principal|executive
    cost_category: str
    capabilities: list[str]
    model_profile: str = "smart"
    name: str = ""  # populated from ROLE_DEFINITIONS key at module load

    @property
    def department(self) -> str:
        return self.department_kind


ROLE_DEFINITIONS: dict[str, RoleDefinition] = {
    "research_analyst": RoleDefinition(
        department_kind="research", seniority="mid", cost_category="analyst",
        capabilities=["web_search", "data_analysis", "knowledge_synthesis", "report_generation"],
    ),
    "data_scientist": RoleDefinition(
        department_kind="data", seniority="senior", cost_category="analyst",
        capabilities=["data_analysis", "financial_modeling", "forecasting", "report_generation"],
        model_profile="analytical",
    ),
    "software_engineer": RoleDefinition(
        department_kind="engineering", seniority="mid", cost_category="engineer",
        capabilities=["code_generation", "code_review", "api_design"],
        model_profile="coding",
    ),
    "senior_engineer": RoleDefinition(
        department_kind="engineering", seniority="senior", cost_category="senior_engineer",
        capabilities=["code_generation", "code_review", "database_design", "api_design",
                       "infrastructure_management"],
        model_profile="coding",
    ),
    "content_writer": RoleDefinition(
        department_kind="content", seniority="mid", cost_category="writer",
        capabilities=["content_writing", "copywriting", "technical_writing"],
        model_profile="creative",
    ),
    "marketing_manager": RoleDefinition(
        department_kind="marketing", seniority="senior", cost_category="specialist",
        capabilities=["campaign_management", "seo_optimization", "social_media",
                       "competitive_intelligence", "email_marketing"],
        model_profile="creative",
    ),
    "financial_analyst": RoleDefinition(
        department_kind="finance", seniority="mid", cost_category="analyst",
        capabilities=["financial_modeling", "budget_planning", "forecasting", "data_analysis"],
        model_profile="analytical",
    ),
    "legal_specialist": RoleDefinition(
        department_kind="legal", seniority="senior", cost_category="specialist",
        capabilities=["legal_review", "contract_analysis", "compliance_check"],
        model_profile="expert",
    ),
    "hr_specialist": RoleDefinition(
        department_kind="hr", seniority="mid", cost_category="specialist",
        capabilities=["hr_screening", "talent_assessment", "performance_analysis"],
        model_profile="smart",
    ),
    "security_engineer": RoleDefinition(
        department_kind="security", seniority="senior", cost_category="senior_engineer",
        capabilities=["security_audit", "threat_analysis", "incident_response"],
        model_profile="expert",
    ),
    "customer_support_agent": RoleDefinition(
        department_kind="support", seniority="junior", cost_category="support",
        capabilities=["customer_support", "knowledge_search", "email_outreach"],
        model_profile="fast",
    ),
    "project_manager": RoleDefinition(
        department_kind="operations", seniority="senior", cost_category="specialist",
        capabilities=["project_planning", "workflow_optimization", "meeting_facilitation",
                       "report_generation"],
        model_profile="smart",
    ),
    "executive_assistant": RoleDefinition(
        department_kind="executive", seniority="mid", cost_category="specialist",
        capabilities=["calendar_management", "meeting_facilitation", "executive_briefing",
                       "document_analysis"],
        model_profile="smart",
    ),
    "strategy_director": RoleDefinition(
        department_kind="executive", seniority="principal", cost_category="executive",
        capabilities=["strategy_formulation", "scenario_planning", "decision_analysis",
                       "executive_briefing", "competitive_intelligence"],
        model_profile="premium",
    ),
    "product_manager": RoleDefinition(
        department_kind="product", seniority="senior", cost_category="specialist",
        capabilities=["product_analysis", "user_research", "decision_analysis",
                       "market_research"],
        model_profile="analytical",
    ),
    "rpa_developer": RoleDefinition(
        department_kind="operations", seniority="mid", cost_category="engineer",
        capabilities=["rpa_automation", "web_scraping", "workflow_optimization"],
        model_profile="worker",
    ),
    "procurement_analyst": RoleDefinition(
        department_kind="operations", seniority="mid", cost_category="analyst",
        capabilities=["procurement_analysis", "vendor_evaluation", "contract_analysis"],
        model_profile="analytical",
    ),
    "knowledge_manager": RoleDefinition(
        department_kind="research", seniority="mid", cost_category="analyst",
        capabilities=["knowledge_management", "knowledge_synthesis", "document_analysis"],
        model_profile="smart",
    ),
    "compliance_officer": RoleDefinition(
        department_kind="legal", seniority="senior", cost_category="specialist",
        capabilities=["compliance_check", "risk_assessment", "audit_support",
                       "data_governance"],
        model_profile="expert",
    ),
    "seo_specialist": RoleDefinition(
        department_kind="marketing", seniority="mid", cost_category="analyst",
        capabilities=["seo_optimization", "content_writing", "web_search",
                       "competitive_intelligence"],
        model_profile="analytical",
    ),
    "lead_generation_agent": RoleDefinition(
        department_kind="sales", seniority="junior", cost_category="support",
        capabilities=["lead_generation", "email_outreach", "web_search",
                       "customer_support"],
        model_profile="fast",
    ),
    "financial_controller": RoleDefinition(
        department_kind="finance", seniority="principal", cost_category="executive",
        capabilities=["audit_support", "tax_analysis", "financial_modeling",
                       "risk_assessment", "budget_planning"],
        model_profile="expert",
    ),
    "devops_engineer": RoleDefinition(
        department_kind="engineering", seniority="senior", cost_category="senior_engineer",
        capabilities=["infrastructure_management", "incident_response", "rpa_automation",
                       "code_review"],
        model_profile="coding",
    ),
    "localization_specialist": RoleDefinition(
        department_kind="content", seniority="mid", cost_category="specialist",
        capabilities=["translation", "localization", "content_writing"],
        model_profile="smart",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
#  Keyword → capability heuristics (LLM fallback)
# ─────────────────────────────────────────────────────────────────────────────
GOAL_CAPABILITY_HEURISTICS: dict[str, list[str]] = {
    "research": ["web_search", "knowledge_synthesis", "data_analysis"],
    "market": ["market_research", "competitive_intelligence", "data_analysis"],
    "competitor": ["competitive_intelligence", "web_search", "market_research"],
    "code": ["code_generation", "code_review", "api_design"],
    "develop": ["code_generation", "api_design", "database_design"],
    "write": ["content_writing", "technical_writing", "report_generation"],
    "content": ["content_writing", "copywriting", "seo_optimization"],
    "finance": ["financial_modeling", "budget_planning", "data_analysis"],
    "budget": ["budget_planning", "financial_modeling", "forecasting"],
    "legal": ["legal_review", "contract_analysis", "compliance_check"],
    "contract": ["contract_analysis", "legal_review"],
    "compliance": ["compliance_check", "risk_assessment", "audit_support"],
    "security": ["security_audit", "threat_analysis", "risk_assessment"],
    "automate": ["rpa_automation", "workflow_optimization", "code_generation"],
    "scrape": ["web_scraping", "data_analysis"],
    "analyse": ["data_analysis", "report_generation", "performance_analysis"],
    "analyze": ["data_analysis", "report_generation", "performance_analysis"],
    "report": ["report_generation", "data_analysis"],
    "email": ["email_marketing", "email_outreach", "campaign_management"],
    "campaign": ["campaign_management", "email_marketing", "social_media"],
    "social": ["social_media", "content_writing", "campaign_management"],
    "seo": ["seo_optimization", "content_writing", "web_search"],
    "hr": ["hr_screening", "talent_assessment", "performance_analysis"],
    "recruit": ["hr_screening", "talent_assessment", "lead_generation"],
    "forecast": ["forecasting", "data_analysis", "financial_modeling"],
    "strategy": ["strategy_formulation", "scenario_planning", "decision_analysis"],
    "decision": ["decision_analysis", "risk_assessment", "scenario_planning"],
    "document": ["document_analysis", "ocr_extraction", "knowledge_synthesis"],
    "translate": ["translation", "localization"],
    "presentation": ["presentation_creation", "executive_briefing"],
    "procurement": ["procurement_analysis", "vendor_evaluation"],
    "vendor": ["vendor_evaluation", "procurement_analysis"],
    "product": ["product_analysis", "user_research", "market_research"],
    "user": ["user_research", "product_analysis"],
    "knowledge": ["knowledge_search", "knowledge_synthesis", "knowledge_management"],
    "image": ["image_analysis", "ocr_extraction"],
    "data": ["data_analysis", "data_governance", "report_generation"],
    "project": ["project_planning", "workflow_optimization", "report_generation"],
    "plan": ["project_planning", "scenario_planning", "strategy_formulation"],
    "incident": ["incident_response", "threat_analysis", "security_audit"],
    "tax": ["tax_analysis", "financial_modeling", "compliance_check"],
    "audit": ["audit_support", "compliance_check", "data_governance"],
    "launch": ["campaign_management", "market_research", "strategy_formulation",
                "content_writing", "legal_review"],
    "deploy": ["infrastructure_management", "code_review", "incident_response"],
    "germany": ["localization", "translation", "compliance_check", "market_research"],
    "customer": ["customer_support", "user_research", "sales_analysis"],
    "sales": ["sales_analysis", "lead_generation", "email_outreach"],
    "hiring": ["hr_screening", "talent_assessment"],
    "board": ["executive_briefing", "financial_modeling", "scenario_planning"],
}


# ─────────────────────────────────────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RoleAssignment:
    role_name: str
    department_kind: str
    seniority: str
    capabilities: list[str]
    model_profile: str
    estimated_cost_usd: float
    estimated_hours: float
    priority: int  # 1=essential, 2=important, 3=nice-to-have

    @property
    def department(self) -> str:
        return self.department_kind


@dataclass
class TeamManifest:
    mission_id: str
    team_name: str
    roles: list[RoleAssignment]
    departments: list[str]
    estimated_cost_usd: float
    estimated_duration_hours: float
    risk_level: str
    success_probability: float
    agent_count: int
    requires_human_preview: bool
    formation_reasoning: str
    org_id: str = ""
    tenant_id: str = ""

    @property
    def estimated_agents(self) -> int:
        return self.agent_count


# ─────────────────────────────────────────────────────────────────────────────
#  Engine
# ─────────────────────────────────────────────────────────────────────────────
class TeamFormationEngine:
    """Assembles a TeamManifest from a mission goal."""

    def __init__(self, llm_provider: Any | None = None) -> None:
        self._llm = llm_provider

    async def form_team_for_goal(
        self,
        goal: str,
        org_id: str = "",
        tenant_id: str = "",
        budget_usd: float | None = None,
    ) -> TeamManifest:
        """Convenience entry-point: form a team directly from a goal string."""
        import dataclasses

        @dataclasses.dataclass
        class _SimpleMission:
            goal_text: str
            risk_level: str = "low"
            title: str = ""

        @dataclasses.dataclass
        class _SimpleOrg:
            org_id: str = ""

        manifest = await self.form_team(
            mission=_SimpleMission(goal_text=goal, title=goal[:80]),
            org=_SimpleOrg(org_id=org_id),
        )
        # Attach routing context
        object.__setattr__(manifest, "org_id", org_id) if dataclasses.is_dataclass(manifest) else None
        try:
            manifest.org_id = org_id
            manifest.tenant_id = tenant_id
        except Exception:
            pass
        return manifest

    async def form_team(self, mission: Any, org: Any) -> TeamManifest:
        with _tracer.start_as_current_span("team_formation.form_team") as span:
            span.set_attribute("mission_id", str(getattr(mission, "id", "new")))
            span.set_attribute("org_id", str(getattr(org, "id", "unknown")))
            _log.info("team_formation.start", mission_id=str(getattr(mission, "id", "new")))

            goal_text = str(getattr(mission, "goal_text", "") or getattr(mission, "title", ""))
            capabilities = await self._extract_capabilities(goal_text)
            span.set_attribute("capabilities_count", len(capabilities))

            roles = await self._map_capabilities_to_roles(capabilities)
            dept_map = await self._assign_departments(roles)
            departments = list(dept_map.keys())

            duration_hours = _estimate_duration(roles)
            cost = await self._estimate_cost(roles, duration_hours)
            risk = await self._assess_risk(roles, mission)
            success_prob = _estimate_success_probability(roles, risk, capabilities)

            org_name = str(getattr(org, "name", "Org"))
            mission_title = str(getattr(mission, "title", goal_text[:40]))
            manifest = TeamManifest(
                mission_id=str(getattr(mission, "id", "new")),
                team_name=f"{org_name} — {mission_title[:40]}",
                roles=roles,
                departments=departments,
                estimated_cost_usd=round(cost, 4),
                estimated_duration_hours=round(duration_hours, 2),
                risk_level=risk,
                success_probability=success_prob,
                agent_count=len(roles),
                requires_human_preview=False,
                formation_reasoning=(
                    f"Formed {len(roles)} roles across {len(departments)} dept(s) "
                    f"covering {len(capabilities)} required capabilities."
                ),
            )
            manifest.requires_human_preview = await self._should_require_preview(manifest, org)

            span.set_attribute("role_count", len(roles))
            span.set_attribute("risk_level", risk)
            span.set_attribute("estimated_cost_usd", cost)
            _log.info(
                "team_formation.done",
                mission_id=str(getattr(mission, "id", "new")),
                roles=len(roles),
                departments=departments,
                cost=round(cost, 4),
                risk=risk,
                requires_preview=manifest.requires_human_preview,
            )
            return manifest

    async def _extract_capabilities(self, goal_text: str) -> list[str]:
        with _tracer.start_as_current_span("team_formation.extract_capabilities") as span:
            if self._llm is not None:
                try:
                    caps = await self._extract_capabilities_llm(goal_text)
                    span.set_attribute("source", "llm")
                    span.set_attribute("capabilities_found", len(caps))
                    return caps
                except Exception as exc:
                    _log.warning("team_formation.llm_extract_failed", error=str(exc))

            caps = _heuristic_capabilities(goal_text)
            span.set_attribute("source", "heuristic")
            span.set_attribute("capabilities_found", len(caps))
            return caps

    async def _extract_capabilities_llm(self, goal_text: str) -> list[str]:
        prompt = (
            "You are an AI org OS team planner. Given a mission goal, "
            "return a JSON array of required capability names. "
            "Use ONLY capabilities from this list:\n"
            + ", ".join(sorted(CAPABILITY_REGISTRY.keys()))
            + f"\n\nMISSION GOAL: {goal_text}\n\n"
            "Return ONLY a JSON array of strings. No explanation."
        )
        from app.providers.base import CompletionRequest, Message  # noqa: PLC0415
        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model="claude-sonnet-4-5",
            max_tokens=512,
        )
        resp = await self._llm.complete(req)
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        caps: list[str] = json.loads(raw)
        return [c for c in caps if c in CAPABILITY_REGISTRY]

    async def _map_capabilities_to_roles(self, capabilities: list[str]) -> list[RoleAssignment]:
        with _tracer.start_as_current_span("team_formation.map_roles"):
            covered: set[str] = set()
            assigned: list[RoleAssignment] = []

            for role_name, role_def in ROLE_DEFINITIONS.items():
                overlap = set(role_def.capabilities) & set(capabilities) - covered
                if not overlap:
                    continue
                hours = _default_hours(role_def.seniority)
                cost = COST_ESTIMATES.get(role_def.cost_category, 0.15) * hours
                overlap_count = len(overlap)
                assigned.append(
                    RoleAssignment(
                        role_name=role_name,
                        department_kind=role_def.department_kind,
                        seniority=role_def.seniority,
                        capabilities=list(set(role_def.capabilities) & set(capabilities)),
                        model_profile=role_def.model_profile,
                        estimated_cost_usd=round(cost, 4),
                        estimated_hours=hours,
                        priority=_priority_for_overlap(overlap_count, len(capabilities)),
                    )
                )
                covered.update(role_def.capabilities)

            assigned.sort(key=lambda r: r.priority)
            _log.info("team_formation.roles_assigned", role_count=len(assigned))
            return assigned

    async def _assign_departments(
        self, roles: list[RoleAssignment],
    ) -> dict[str, list[RoleAssignment]]:
        dept_map: dict[str, list[RoleAssignment]] = {}
        for role in roles:
            dept_map.setdefault(role.department_kind, []).append(role)
        return dept_map

    async def _estimate_cost(
        self, roles: list[RoleAssignment], duration_hours: float,
    ) -> float:
        return sum(r.estimated_cost_usd for r in roles)

    async def _assess_risk(self, roles: list[RoleAssignment], mission: Any) -> str:
        with _tracer.start_as_current_span("team_formation.assess_risk"):
            high_risk_caps = {
                cap for cap, spec in CAPABILITY_REGISTRY.items()
                if spec.risk_level in ("high", "critical")
            }
            role_caps = {cap for r in roles for cap in r.capabilities}
            risky = role_caps & high_risk_caps
            critical = {
                cap for cap in risky
                if CAPABILITY_REGISTRY.get(cap) and
                   CAPABILITY_REGISTRY[cap].risk_level == "critical"
            }
            if critical:
                return "critical"
            if risky:
                return "high"
            mission_risk = getattr(mission, "risk_level", "low") or "low"
            if mission_risk == "high":
                return "high"
            return "medium" if len(roles) > 5 else "low"

    async def _should_require_preview(self, manifest: TeamManifest, org: Any) -> bool:
        if manifest.risk_level in ("critical", "high"):
            return True
        monthly_budget = float(getattr(org, "monthly_budget_usd", 0) or 0)
        if monthly_budget > 0 and manifest.estimated_cost_usd > monthly_budget * 0.10:
            return True
        autonomy = int(getattr(org, "autonomy_level", 3) or 3)
        if autonomy < 2:
            return True
        return False

    def to_team_creation_requests(self, manifest: TeamManifest) -> list[dict[str, Any]]:
        """Convert manifest to DB-ready operation dicts."""
        ops: list[dict[str, Any]] = [
            {
                "op": "create_team",
                "name": manifest.team_name,
                "mission_id": manifest.mission_id,
                "departments": manifest.departments,
                "estimated_cost_usd": manifest.estimated_cost_usd,
                "requires_preview": manifest.requires_human_preview,
            },
        ]
        for role in manifest.roles:
            ops.append(
                {
                    "op": "create_role",
                    "role_name": role.role_name,
                    "department_kind": role.department_kind,
                    "seniority": role.seniority,
                    "capabilities": role.capabilities,
                    "model_profile": role.model_profile,
                    "estimated_cost_usd": role.estimated_cost_usd,
                    "priority": role.priority,
                }
            )
        return ops


# ─────────────────────────────────────────────────────────────────────────────
#  Private helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── Post-process: assign name from dict key ──────────────────────────────────
for _role_name, _role_def in ROLE_DEFINITIONS.items():
    if not _role_def.name:
        _role_def.name = _role_name


def _heuristic_capabilities(goal_text: str) -> list[str]:
    """Keyword-based capability extraction when LLM unavailable."""
    text = goal_text.lower()
    found: set[str] = set()
    for keyword, caps in GOAL_CAPABILITY_HEURISTICS.items():
        if keyword in text:
            found.update(caps)
    # Always include fallback basics
    if not found:
        found = {"web_search", "knowledge_synthesis", "report_generation"}
    return list(found)


def _default_hours(seniority: str) -> float:
    return {
        "junior": 4.0, "mid": 6.0, "senior": 8.0,
        "principal": 10.0, "executive": 12.0,
    }.get(seniority, 6.0)


def _estimate_duration(roles: list[RoleAssignment]) -> float:
    if not roles:
        return 1.0
    return max(r.estimated_hours for r in roles)


def _priority_for_overlap(overlap_count: int, total_required: int) -> int:
    ratio = overlap_count / max(total_required, 1)
    if ratio >= 0.25:
        return 1  # essential
    if ratio >= 0.10:
        return 2  # important
    return 3       # nice-to-have


def _estimate_success_probability(
    roles: list[RoleAssignment],
    risk: str,
    capabilities: list[str],
) -> float:
    base = {"low": 0.92, "medium": 0.82, "high": 0.68, "critical": 0.50}.get(risk, 0.75)
    covered = {cap for r in roles for cap in r.capabilities}
    coverage = len(covered & set(capabilities)) / max(len(capabilities), 1)
    return round(min(base * (0.5 + 0.5 * coverage), 1.0), 3)


# ─────────────────────────────────────────────────────────────────────────────
#  RoleMapper — maps human role labels to RoleDefinitions
# ─────────────────────────────────────────────────────────────────────────────

class RoleMapper:
    """Resolves a natural-language role name into a RoleDefinition."""

    def resolve(self, role_name: str) -> RoleDefinition | None:
        normalised = role_name.lower().strip().replace(" ", "_")
        return ROLE_DEFINITIONS.get(normalised)

    def list_roles(self) -> list[str]:
        return list(ROLE_DEFINITIONS.keys())

    def map_capabilities(self, capabilities: list[str]) -> list[RoleDefinition]:
        """Return RoleDefinitions that cover all requested capabilities."""
        results: list[RoleDefinition] = []
        seen: set[str] = set()
        for cap in capabilities:
            for role_name, role_def in ROLE_DEFINITIONS.items():
                if cap in (role_def.capabilities or []) and role_name not in seen:
                    results.append(role_def)
                    seen.add(role_name)
        return results
