"""
PART 4 + PART 6 — Complete role taxonomy (22 departments) + OrgAgent lifecycle.

Extends team_formation.py's partial role definitions with all 22 departments
from the spec. Also provides the OrgAgent status state machine (PART 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── PART 6: Agent Status State Machine ───────────────────────────────────────


class AgentStatus(StrEnum):
    """
    Spec PART 6 agent status state machine:
    IDLE → PLANNING → PLAN_READY → EXECUTING → [WAITING_TOOL | WAITING_APPROVAL
                                                  | BLOCKED → ESCALATED | FAILED]
              ↑                          ↓
         COMPLETED ← VERIFYING ←────────┘
    """

    IDLE = "idle"
    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    EXECUTING = "executing"
    WAITING_TOOL = "waiting_tool"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


# Valid transitions per spec
AGENT_TRANSITIONS: dict[AgentStatus, list[AgentStatus]] = {
    AgentStatus.IDLE: [AgentStatus.PLANNING],
    AgentStatus.PLANNING: [AgentStatus.PLAN_READY, AgentStatus.FAILED],
    AgentStatus.PLAN_READY: [AgentStatus.EXECUTING, AgentStatus.IDLE],
    AgentStatus.EXECUTING: [
        AgentStatus.WAITING_TOOL,
        AgentStatus.WAITING_APPROVAL,
        AgentStatus.VERIFYING,
        AgentStatus.BLOCKED,
        AgentStatus.FAILED,
    ],
    AgentStatus.WAITING_TOOL: [AgentStatus.EXECUTING, AgentStatus.FAILED],
    AgentStatus.WAITING_APPROVAL: [AgentStatus.EXECUTING, AgentStatus.FAILED],
    AgentStatus.BLOCKED: [AgentStatus.ESCALATED, AgentStatus.EXECUTING],
    AgentStatus.ESCALATED: [AgentStatus.EXECUTING, AgentStatus.FAILED],
    AgentStatus.VERIFYING: [AgentStatus.COMPLETED, AgentStatus.EXECUTING],
    AgentStatus.COMPLETED: [],
    AgentStatus.FAILED: [AgentStatus.IDLE],  # allow reset
}


def validate_transition(current: AgentStatus, next_status: AgentStatus) -> bool:
    """Returns True if the transition is valid per the spec state machine."""
    return next_status in AGENT_TRANSITIONS.get(current, [])


# ── PART 4: Complete role taxonomy — all 22 departments, 456 roles ────────────


@dataclass
class FullRoleDefinition:
    """Extended role definition covering all PART 4 fields."""

    name: str
    department: str
    seniority: str
    capabilities: list[str] = field(default_factory=list)
    model_profile: str = "smart"
    cost_category: str = "specialist"
    max_task_duration_hours: float = 4.0
    autonomy_level: int = 3
    risk_level: str = "medium"
    quality_threshold: float = 0.80
    decision_authority: list[str] = field(default_factory=list)
    requires_approval_for: list[str] = field(default_factory=list)


# All 22 departments with key roles (representative subset of 456 total)
FULL_ROLE_TAXONOMY: dict[str, list[FullRoleDefinition]] = {
    # ── DEPT 1: EXECUTIVE ────────────────────────────────────────────────────
    "executive": [
        FullRoleDefinition(
            "CEO",
            "executive",
            "executive",
            capabilities=["strategic_planning", "decision_making", "org_management"],
            model_profile="premium",
            cost_category="executive",
            autonomy_level=5,
            quality_threshold=0.97,
        ),
        FullRoleDefinition(
            "CTO",
            "executive",
            "executive",
            capabilities=["technology_strategy", "engineering_oversight"],
            model_profile="premium",
            cost_category="executive",
        ),
        FullRoleDefinition(
            "CFO",
            "executive",
            "executive",
            capabilities=["financial_modeling", "budget_planning", "risk_analysis"],
            model_profile="premium",
            cost_category="executive",
            requires_approval_for=["spend_gt_50k"],
        ),
        FullRoleDefinition(
            "CMO",
            "executive",
            "executive",
            capabilities=["marketing_strategy", "brand_management"],
            model_profile="premium",
            cost_category="executive",
        ),
        FullRoleDefinition(
            "Chief AI Officer",
            "executive",
            "executive",
            capabilities=["ai_strategy", "model_governance"],
            model_profile="premium",
            cost_category="executive",
        ),
    ],
    # ── DEPT 2: STRATEGY & BUSINESS ──────────────────────────────────────────
    "strategy_business": [
        FullRoleDefinition(
            "Business Strategist",
            "strategy_business",
            "senior",
            capabilities=["strategic_planning", "competitive_intelligence", "market_analysis"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Market Analyst",
            "strategy_business",
            "mid",
            capabilities=["market_analysis", "web_search", "data_analysis"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Competitive Intelligence Agent",
            "strategy_business",
            "mid",
            capabilities=["competitive_intelligence", "web_search"],
            model_profile="research",
        ),
        FullRoleDefinition(
            "Business Development Agent",
            "strategy_business",
            "senior",
            capabilities=["partnership_management", "opportunity_analysis"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Growth Strategist",
            "strategy_business",
            "senior",
            capabilities=["growth_hacking", "funnel_optimization"],
            model_profile="analytical",
        ),
    ],
    # ── DEPT 3: PRODUCT ──────────────────────────────────────────────────────
    "product": [
        FullRoleDefinition(
            "Product Manager",
            "product",
            "senior",
            capabilities=["product_strategy", "roadmap_management", "user_research"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Product Owner",
            "product",
            "mid",
            capabilities=["backlog_management", "sprint_planning"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "User Researcher",
            "product",
            "mid",
            capabilities=["user_research", "survey_design", "data_analysis"],
            model_profile="smart",
        ),
    ],
    # ── DEPT 4: ENGINEERING ──────────────────────────────────────────────────
    "engineering": [
        FullRoleDefinition(
            "Software Architect",
            "engineering",
            "principal",
            capabilities=["architecture_design", "code_review", "database_design"],
            model_profile="coding",
            quality_threshold=0.95,
        ),
        FullRoleDefinition(
            "Backend Engineer",
            "engineering",
            "senior",
            capabilities=["code_generation", "database_design", "api_design"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "Frontend Engineer",
            "engineering",
            "senior",
            capabilities=["frontend_development", "ui_design", "accessibility"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "Platform Engineer",
            "engineering",
            "senior",
            capabilities=["infrastructure_management", "cicd", "kubernetes"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "SDK Engineer",
            "engineering",
            "senior",
            capabilities=["code_generation", "api_design", "documentation_writing"],
            model_profile="coding",
        ),
    ],
    # ── DEPT 5: AI/ML ────────────────────────────────────────────────────────
    "ai_ml": [
        FullRoleDefinition(
            "AI Architect",
            "ai_ml",
            "principal",
            capabilities=["ai_strategy", "model_governance", "architecture_design"],
            model_profile="expert",
            quality_threshold=0.97,
        ),
        FullRoleDefinition(
            "LLM Engineer",
            "ai_ml",
            "senior",
            capabilities=["fine_tuning", "prompt_engineering", "model_evaluation"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "RAG Engineer",
            "ai_ml",
            "senior",
            capabilities=["knowledge_synthesis", "vector_search", "data_ingestion"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "AI Safety Engineer",
            "ai_ml",
            "senior",
            capabilities=["security_audit", "guardrails_design"],
            model_profile="expert",
            quality_threshold=0.99,
        ),
    ],
    # ── DEPT 6: MARKETING ────────────────────────────────────────────────────
    "marketing": [
        FullRoleDefinition(
            "Content Strategist",
            "marketing",
            "senior",
            capabilities=["content_strategy", "seo_optimization"],
            model_profile="creative",
        ),
        FullRoleDefinition(
            "Copywriter",
            "marketing",
            "mid",
            capabilities=["copywriting", "content_writing"],
            model_profile="creative",
        ),
        FullRoleDefinition(
            "SEO Strategist",
            "marketing",
            "mid",
            capabilities=["seo_optimization", "keyword_research"],
            model_profile="analytical",
        ),
        FullRoleDefinition(
            "Growth Marketer",
            "marketing",
            "senior",
            capabilities=["growth_hacking", "conversion_optimization"],
            model_profile="analytical",
        ),
        FullRoleDefinition(
            "Performance Marketing Agent",
            "marketing",
            "mid",
            capabilities=["paid_advertising", "campaign_management"],
            model_profile="analytical",
        ),
        FullRoleDefinition(
            "PR Manager",
            "marketing",
            "senior",
            capabilities=["public_relations", "crisis_communications"],
            model_profile="creative",
            requires_approval_for=["press_releases", "public_statements"],
        ),
    ],
    # ── DEPT 7: SALES ────────────────────────────────────────────────────────
    "sales": [
        FullRoleDefinition(
            "Account Executive",
            "sales",
            "senior",
            capabilities=["sales_outreach", "proposal_writing", "negotiation"],
            model_profile="fast",
        ),
        FullRoleDefinition(
            "SDR",
            "sales",
            "junior",
            capabilities=["lead_generation", "email_outreach"],
            model_profile="fast",
            requires_approval_for=["email_send_gt_100"],
        ),
        FullRoleDefinition(
            "Sales Intelligence Analyst",
            "sales",
            "mid",
            capabilities=["pipeline_analysis", "forecasting"],
            model_profile="analytical",
        ),
    ],
    # ── DEPT 8: FINANCE ──────────────────────────────────────────────────────
    "finance": [
        FullRoleDefinition(
            "Financial Analyst",
            "finance",
            "mid",
            capabilities=["financial_modeling", "data_analysis"],
            model_profile="analytical",
            quality_threshold=0.97,
        ),
        FullRoleDefinition(
            "FP&A Agent",
            "finance",
            "senior",
            capabilities=["forecasting", "budget_planning"],
            model_profile="analytical",
            requires_approval_for=["spend_gt_5000"],
        ),
        FullRoleDefinition(
            "Invoice Processing Agent",
            "finance",
            "junior",
            capabilities=["invoice_processing", "ocr_extraction"],
            model_profile="fast",
        ),
    ],
    # ── DEPT 9: HR/PEOPLE ────────────────────────────────────────────────────
    "hr": [
        FullRoleDefinition(
            "Talent Acquisition Agent",
            "hr",
            "mid",
            capabilities=["candidate_research", "talent_assessment"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Employee Support Agent",
            "hr",
            "junior",
            capabilities=["customer_support", "knowledge_search"],
            model_profile="fast",
        ),
        FullRoleDefinition(
            "People Analytics Agent",
            "hr",
            "mid",
            capabilities=["data_analysis", "report_generation"],
            model_profile="analytical",
        ),
    ],
    # ── DEPT 10: OPERATIONS ──────────────────────────────────────────────────
    "operations": [
        FullRoleDefinition(
            "Operations Manager",
            "operations",
            "senior",
            capabilities=["project_planning", "process_optimization"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Vendor Management Agent",
            "operations",
            "mid",
            capabilities=["vendor_research", "negotiation"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Scheduling Agent",
            "operations",
            "junior",
            capabilities=["calendar_management", "meeting_facilitation"],
            model_profile="fast",
        ),
    ],
    # ── DEPT 11: DEVOPS/SRE ──────────────────────────────────────────────────
    "devops": [
        FullRoleDefinition(
            "SRE",
            "devops",
            "senior",
            capabilities=["infrastructure_management", "incident_response"],
            model_profile="coding",
            quality_threshold=0.95,
        ),
        FullRoleDefinition(
            "DevSecOps Engineer",
            "devops",
            "senior",
            capabilities=["security_audit", "cicd", "threat_analysis"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "Incident Response Engineer",
            "devops",
            "senior",
            capabilities=["incident_response", "root_cause_analysis"],
            model_profile="coding",
        ),
    ],
    # ── DEPT 12: CUSTOMER SUCCESS ────────────────────────────────────────────
    "customer_success": [
        FullRoleDefinition(
            "Customer Success Manager",
            "customer_success",
            "senior",
            capabilities=["customer_support", "knowledge_search", "retention_analysis"],
            model_profile="fast",
        ),
        FullRoleDefinition(
            "Technical Support Agent",
            "customer_success",
            "mid",
            capabilities=["technical_support", "documentation_writing"],
            model_profile="fast",
        ),
        FullRoleDefinition(
            "Voice-of-Customer Agent",
            "customer_success",
            "mid",
            capabilities=["survey_design", "data_analysis", "report_generation"],
            model_profile="analytical",
        ),
    ],
    # ── DEPT 13: DESIGN/CREATIVE ─────────────────────────────────────────────
    "design": [
        FullRoleDefinition(
            "Creative Director",
            "design",
            "senior",
            capabilities=["brand_management", "creative_strategy"],
            model_profile="creative",
        ),
        FullRoleDefinition(
            "UI Designer",
            "design",
            "mid",
            capabilities=["ui_design", "accessibility"],
            model_profile="creative",
        ),
        FullRoleDefinition(
            "Motion Designer",
            "design",
            "mid",
            capabilities=["animation", "video_production"],
            model_profile="creative",
        ),
    ],
    # ── DEPT 14: WRITING/EDITORIAL ───────────────────────────────────────────
    "writing": [
        FullRoleDefinition(
            "Technical Writer",
            "writing",
            "mid",
            capabilities=["technical_writing", "documentation_writing"],
            model_profile="creative",
        ),
        FullRoleDefinition(
            "Ghostwriter",
            "writing",
            "senior",
            capabilities=["content_writing", "copywriting"],
            model_profile="creative",
        ),
    ],
    # ── DEPT 15: DATA ────────────────────────────────────────────────────────
    "data": [
        FullRoleDefinition(
            "Data Architect",
            "data",
            "principal",
            capabilities=["database_design", "data_governance"],
            model_profile="analytical",
            quality_threshold=0.95,
        ),
        FullRoleDefinition(
            "Data Engineer",
            "data",
            "senior",
            capabilities=["data_ingestion", "pipeline_design"],
            model_profile="coding",
        ),
        FullRoleDefinition(
            "BI Analyst",
            "data",
            "mid",
            capabilities=["data_analysis", "visualization", "report_generation"],
            model_profile="analytical",
        ),
    ],
    # ── DEPT 16: RESEARCH ────────────────────────────────────────────────────
    "research": [
        FullRoleDefinition(
            "Research Scientist",
            "research",
            "senior",
            capabilities=["literature_research", "hypothesis_generation", "experiment_design"],
            model_profile="research",
        ),
        FullRoleDefinition(
            "Market Researcher",
            "research",
            "mid",
            capabilities=["web_search", "survey_design", "competitive_intelligence"],
            model_profile="research",
        ),
        FullRoleDefinition(
            "Fact Checker",
            "research",
            "mid",
            capabilities=["fact_checking", "source_verification"],
            model_profile="research",
        ),
    ],
    # ── DEPT 17: LEGAL/COMPLIANCE ────────────────────────────────────────────
    "legal": [
        FullRoleDefinition(
            "General Counsel",
            "legal",
            "executive",
            capabilities=["legal_review", "contract_analysis", "regulatory_research"],
            model_profile="expert",
            quality_threshold=0.99,
            requires_approval_for=["legal_binding_agreements"],
        ),
        FullRoleDefinition(
            "Compliance Officer",
            "legal",
            "senior",
            capabilities=["compliance_check", "regulatory_research", "gdpr_analysis"],
            model_profile="expert",
            quality_threshold=0.99,
        ),
        FullRoleDefinition(
            "Privacy Specialist",
            "legal",
            "senior",
            capabilities=["privacy_analysis", "gdpr_analysis", "data_governance"],
            model_profile="expert",
        ),
    ],
    # ── DEPT 18: SECURITY ────────────────────────────────────────────────────
    "security": [
        FullRoleDefinition(
            "CISO",
            "security",
            "executive",
            capabilities=["security_strategy", "security_audit"],
            model_profile="expert",
            quality_threshold=0.99,
        ),
        FullRoleDefinition(
            "Threat Intelligence Analyst",
            "security",
            "senior",
            capabilities=["threat_analysis", "security_audit"],
            model_profile="expert",
        ),
        FullRoleDefinition(
            "Red Team Agent",
            "security",
            "senior",
            capabilities=["penetration_testing", "security_audit"],
            model_profile="expert",
            requires_approval_for=["external_systems_access"],
        ),
    ],
    # ── DEPT 19: QA/QUALITY ──────────────────────────────────────────────────
    "qa": [
        FullRoleDefinition(
            "QA Architect",
            "qa",
            "principal",
            capabilities=["test_design", "quality_assurance"],
            model_profile="analytical",
        ),
        FullRoleDefinition(
            "LLM Evaluator",
            "qa",
            "senior",
            capabilities=["llm_evaluation", "hallucination_detection"],
            model_profile="analytical",
        ),
        FullRoleDefinition(
            "Chaos Engineer",
            "qa",
            "senior",
            capabilities=["chaos_testing", "reliability_testing"],
            model_profile="coding",
        ),
    ],
    # ── DEPT 20: PROCUREMENT ─────────────────────────────────────────────────
    "procurement": [
        FullRoleDefinition(
            "Vendor Research Agent",
            "procurement",
            "mid",
            capabilities=["vendor_research", "web_search"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Negotiation Support Agent",
            "procurement",
            "senior",
            capabilities=["negotiation", "contract_analysis"],
            model_profile="smart",
            requires_approval_for=["contracts_gt_10k"],
        ),
    ],
    # ── DEPT 21: PMO ─────────────────────────────────────────────────────────
    "pmo": [
        FullRoleDefinition(
            "Program Manager",
            "pmo",
            "senior",
            capabilities=["project_planning", "dependency_management"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Scrum Master",
            "pmo",
            "mid",
            capabilities=["agile_coaching", "sprint_planning"],
            model_profile="smart",
        ),
    ],
    # ── DEPT 22: KNOWLEDGE MANAGEMENT ───────────────────────────────────────
    "knowledge_management": [
        FullRoleDefinition(
            "Knowledge Engineer",
            "knowledge_management",
            "senior",
            capabilities=["knowledge_synthesis", "ontology_design"],
            model_profile="smart",
        ),
        FullRoleDefinition(
            "Knowledge Curator",
            "knowledge_management",
            "mid",
            capabilities=["knowledge_synthesis", "content_review"],
            model_profile="smart",
        ),
    ],
}


def get_role(department: str, role_name: str) -> FullRoleDefinition | None:
    """Look up a role by department and name."""
    for role in FULL_ROLE_TAXONOMY.get(department, []):
        if role.name.lower() == role_name.lower():
            return role
    return None


def get_roles_for_dept(department: str) -> list[FullRoleDefinition]:
    """Return all roles for a department."""
    return FULL_ROLE_TAXONOMY.get(department, [])


def count_total_roles() -> int:
    return sum(len(roles) for roles in FULL_ROLE_TAXONOMY.values())
