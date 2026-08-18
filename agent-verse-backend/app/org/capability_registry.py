"""Capability Registry — 60+ named capabilities with tool/model/quality bindings.

Every capability maps to: required tools, preferred model profiles, minimum quality,
risk level, and whether human approval is needed.

Used by TeamFormationEngine and MetaOrchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class CapabilitySpec:
    tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)  # preferred model profile names
    vision: bool = False
    human_approval: bool = False
    min_quality: float = 0.75
    risk_level: str = "low"  # low|medium|high|critical
    description: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Master registry — 60+ capabilities
# ─────────────────────────────────────────────────────────────────────────────
CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = {
    # ── Research & Analysis ────────────────────────────────────────────────
    "web_search": CapabilitySpec(
        tools=["brave_search", "serp"], risk_level="low",
        description="Search the web for information",
    ),
    "data_analysis": CapabilitySpec(
        tools=["sql", "python"], models=["analytical"], min_quality=0.85,
        description="Analyse structured and unstructured data",
    ),
    "competitive_intelligence": CapabilitySpec(
        tools=["brave_search", "serp"], models=["research"],
        description="Gather and synthesise competitor information",
    ),
    "market_research": CapabilitySpec(
        tools=["brave_search"], models=["research"],
        description="Research market trends, segments and opportunities",
    ),
    "financial_modeling": CapabilitySpec(
        tools=["python", "sql"], models=["analytical"], min_quality=0.90,
        description="Build financial projections and models",
    ),
    "legal_review": CapabilitySpec(
        tools=[], models=["expert"], min_quality=0.95, human_approval=True,
        risk_level="critical", description="Review contracts and legal documents",
    ),
    "contract_analysis": CapabilitySpec(
        tools=[], models=["expert"], min_quality=0.92, human_approval=True,
        risk_level="high", description="Analyse contract terms and obligations",
    ),
    "compliance_check": CapabilitySpec(
        tools=[], models=["expert"], min_quality=0.95, human_approval=True,
        risk_level="critical", description="Verify regulatory compliance",
    ),
    "risk_assessment": CapabilitySpec(
        tools=["python"], models=["analytical"], min_quality=0.88,
        risk_level="high", description="Identify and quantify risks",
    ),
    # ── Engineering & Technology ───────────────────────────────────────────
    "code_generation": CapabilitySpec(
        tools=["python", "github"], models=["coding"], min_quality=0.85,
        description="Write, complete or fix source code",
    ),
    "code_review": CapabilitySpec(
        tools=["github"], models=["coding"], min_quality=0.88,
        description="Review code for correctness, security and style",
    ),
    "infrastructure_management": CapabilitySpec(
        tools=["bash", "k8s"], models=["coding"],
        risk_level="high", human_approval=True,
        description="Manage cloud and on-prem infrastructure",
    ),
    "database_design": CapabilitySpec(
        tools=["sql", "python"], models=["analytical"],
        description="Design schemas, migrations and query patterns",
    ),
    "api_design": CapabilitySpec(
        tools=[], models=["coding"],
        description="Design REST, GraphQL or gRPC API contracts",
    ),
    "security_audit": CapabilitySpec(
        tools=["bash"], models=["expert"], min_quality=0.95,
        risk_level="high", human_approval=True,
        description="Audit code and systems for security vulnerabilities",
    ),
    "threat_analysis": CapabilitySpec(
        tools=[], models=["expert"], min_quality=0.92,
        risk_level="high", description="Analyse threat landscape and attack vectors",
    ),
    "incident_response": CapabilitySpec(
        tools=["bash", "k8s"], models=["coding"], min_quality=0.90,
        risk_level="critical", human_approval=True,
        description="Respond to and remediate incidents",
    ),
    # ── Content & Marketing ────────────────────────────────────────────────
    "content_writing": CapabilitySpec(
        tools=[], models=["creative"],
        description="Write blog posts, articles and long-form content",
    ),
    "copywriting": CapabilitySpec(
        tools=[], models=["creative"],
        description="Write persuasive marketing copy",
    ),
    "technical_writing": CapabilitySpec(
        tools=[], models=["smart"],
        description="Write technical documentation and guides",
    ),
    "seo_optimization": CapabilitySpec(
        tools=["brave_search", "serp"], models=["analytical"],
        description="Optimise content for search engine ranking",
    ),
    "social_media": CapabilitySpec(
        tools=[], models=["creative"],
        description="Create and schedule social media content",
    ),
    "email_marketing": CapabilitySpec(
        tools=[], models=["creative"],
        description="Write and manage email marketing campaigns",
    ),
    "campaign_management": CapabilitySpec(
        tools=[], models=["analytical"],
        description="Plan and coordinate marketing campaigns",
    ),
    "lead_generation": CapabilitySpec(
        tools=["brave_search", "email_outreach"], models=["research"],
        description="Identify and qualify potential customers",
    ),
    "presentation_creation": CapabilitySpec(
        tools=["python"], models=["creative"],
        description="Create slide decks and visual presentations",
    ),
    # ── Sales & Customer ───────────────────────────────────────────────────
    "sales_analysis": CapabilitySpec(
        tools=["sql", "python"], models=["analytical"],
        description="Analyse sales pipeline and performance",
    ),
    "customer_support": CapabilitySpec(
        tools=[], models=["fast"],
        description="Handle customer queries and issues",
    ),
    "email_outreach": CapabilitySpec(
        tools=["email_outreach"], models=["creative"],
        risk_level="medium", human_approval=True,
        description="Send personalised outreach emails",
    ),
    # ── HR & People ────────────────────────────────────────────────────────
    "hr_screening": CapabilitySpec(
        tools=[], models=["smart"], human_approval=True,
        risk_level="medium", description="Screen job candidates",
    ),
    "talent_assessment": CapabilitySpec(
        tools=[], models=["smart"], human_approval=True,
        risk_level="medium", description="Assess skills and suitability of candidates",
    ),
    # ── Finance & Procurement ──────────────────────────────────────────────
    "procurement_analysis": CapabilitySpec(
        tools=["sql", "python"], models=["analytical"],
        description="Analyse procurement spend and vendor performance",
    ),
    "vendor_evaluation": CapabilitySpec(
        tools=["brave_search"], models=["research"],
        description="Evaluate and compare vendors",
    ),
    "budget_planning": CapabilitySpec(
        tools=["python", "sql"], models=["analytical"], min_quality=0.90,
        risk_level="medium", description="Plan and forecast budgets",
    ),
    "tax_analysis": CapabilitySpec(
        tools=[], models=["expert"], min_quality=0.95, human_approval=True,
        risk_level="high", description="Analyse tax obligations and strategies",
    ),
    "audit_support": CapabilitySpec(
        tools=["sql"], models=["analytical"], min_quality=0.93,
        risk_level="high", human_approval=True,
        description="Support financial or compliance audits",
    ),
    # ── Operations & Planning ──────────────────────────────────────────────
    "project_planning": CapabilitySpec(
        tools=[], models=["smart"],
        description="Create project plans, timelines and milestones",
    ),
    "workflow_optimization": CapabilitySpec(
        tools=["python"], models=["analytical"],
        description="Identify and implement process improvements",
    ),
    "calendar_management": CapabilitySpec(
        tools=["calendar"], models=["fast"],
        description="Schedule meetings and manage calendars",
    ),
    "meeting_facilitation": CapabilitySpec(
        tools=[], models=["smart"],
        description="Prepare agendas and facilitate meetings",
    ),
    "forecasting": CapabilitySpec(
        tools=["python", "sql"], models=["analytical"], min_quality=0.85,
        description="Build time-series and demand forecasts",
    ),
    "scenario_planning": CapabilitySpec(
        tools=["python"], models=["analytical"],
        description="Evaluate strategic scenarios and outcomes",
    ),
    "strategy_formulation": CapabilitySpec(
        tools=[], models=["premium"], min_quality=0.90, human_approval=True,
        risk_level="medium", description="Formulate organisational strategies",
    ),
    "executive_briefing": CapabilitySpec(
        tools=[], models=["premium"], min_quality=0.90,
        description="Prepare concise executive briefings",
    ),
    "decision_analysis": CapabilitySpec(
        tools=["python"], models=["analytical"], min_quality=0.88,
        description="Structure and analyse decisions with trade-offs",
    ),
    # ── Vision & Document ──────────────────────────────────────────────────
    "image_analysis": CapabilitySpec(
        tools=[], models=["vision"], vision=True,
        description="Analyse images and visual content",
    ),
    "document_analysis": CapabilitySpec(
        tools=[], models=["smart"],
        description="Extract and summarise information from documents",
    ),
    "ocr_extraction": CapabilitySpec(
        tools=["python"], models=["vision"], vision=True,
        description="Extract text from scanned documents and images",
    ),
    # ── Automation ─────────────────────────────────────────────────────────
    "rpa_automation": CapabilitySpec(
        tools=["playwright"], models=["worker"],
        risk_level="medium", description="Automate repetitive browser and UI tasks",
    ),
    "web_scraping": CapabilitySpec(
        tools=["playwright", "python"], models=["worker"],
        description="Scrape structured data from websites",
    ),
    # ── Knowledge ─────────────────────────────────────────────────────────
    "knowledge_search": CapabilitySpec(
        tools=["knowledge_base"], models=["smart"],
        description="Search internal knowledge bases",
    ),
    "knowledge_synthesis": CapabilitySpec(
        tools=["knowledge_base"], models=["smart"],
        description="Synthesise information across multiple sources",
    ),
    "knowledge_management": CapabilitySpec(
        tools=["knowledge_base"], models=["smart"],
        description="Organise and maintain knowledge assets",
    ),
    # ── Analytics & Reporting ──────────────────────────────────────────────
    "performance_analysis": CapabilitySpec(
        tools=["sql", "python"], models=["analytical"],
        description="Analyse team and system performance metrics",
    ),
    "product_analysis": CapabilitySpec(
        tools=["sql", "python"], models=["analytical"],
        description="Analyse product usage and metrics",
    ),
    "user_research": CapabilitySpec(
        tools=[], models=["research"],
        description="Conduct and synthesise user research",
    ),
    "report_generation": CapabilitySpec(
        tools=["python", "sql"], models=["analytical"],
        description="Generate structured reports from data",
    ),
    # ── Governance ─────────────────────────────────────────────────────────
    "data_governance": CapabilitySpec(
        tools=["sql"], models=["analytical"], min_quality=0.90,
        risk_level="high", human_approval=True,
        description="Define and enforce data governance policies",
    ),
    # ── Localisation ──────────────────────────────────────────────────────
    "translation": CapabilitySpec(
        tools=[], models=["smart"],
        description="Translate content across languages",
    ),
    "localization": CapabilitySpec(
        tools=[], models=["smart"],
        description="Adapt content for regional markets",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
#  Department → capability mapping
# ─────────────────────────────────────────────────────────────────────────────
DEPT_CAPABILITY_MAP: dict[str, list[str]] = {
    "executive": [
        "strategy_formulation", "decision_analysis", "scenario_planning",
        "executive_briefing", "financial_modeling", "risk_assessment",
        "meeting_facilitation", "forecasting",
    ],
    "engineering": [
        "code_generation", "code_review", "infrastructure_management",
        "database_design", "api_design", "security_audit", "threat_analysis",
        "incident_response",
    ],
    "marketing": [
        "content_writing", "copywriting", "social_media", "email_marketing",
        "campaign_management", "lead_generation", "seo_optimization",
        "competitive_intelligence", "market_research", "presentation_creation",
    ],
    "sales": [
        "sales_analysis", "lead_generation", "email_outreach",
        "customer_support", "competitive_intelligence",
    ],
    "finance": [
        "financial_modeling", "budget_planning", "forecasting",
        "tax_analysis", "audit_support", "procurement_analysis",
        "data_analysis", "risk_assessment",
    ],
    "legal": [
        "legal_review", "contract_analysis", "compliance_check",
        "risk_assessment", "data_governance",
    ],
    "hr": [
        "hr_screening", "talent_assessment", "knowledge_management",
        "performance_analysis", "report_generation",
    ],
    "operations": [
        "project_planning", "workflow_optimization", "calendar_management",
        "meeting_facilitation", "report_generation", "procurement_analysis",
        "vendor_evaluation", "rpa_automation",
    ],
    "research": [
        "web_search", "market_research", "competitive_intelligence",
        "user_research", "knowledge_synthesis", "knowledge_search",
        "data_analysis", "document_analysis",
    ],
    "data": [
        "data_analysis", "data_governance", "report_generation",
        "knowledge_management", "ocr_extraction", "document_analysis",
        "performance_analysis",
    ],
    "security": [
        "security_audit", "threat_analysis", "incident_response",
        "compliance_check", "risk_assessment",
    ],
    "support": [
        "customer_support", "knowledge_search", "email_outreach",
        "document_analysis",
    ],
    "content": [
        "content_writing", "copywriting", "technical_writing",
        "translation", "localization", "presentation_creation",
        "report_generation",
    ],
    "product": [
        "product_analysis", "user_research", "competitive_intelligence",
        "market_research", "decision_analysis", "scenario_planning",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
#  Role → capability mapping
# ─────────────────────────────────────────────────────────────────────────────
ROLE_CAPABILITY_MAP: dict[str, list[str]] = {
    "CEO": ["strategy_formulation", "executive_briefing", "decision_analysis",
             "scenario_planning", "financial_modeling"],
    "CTO": ["infrastructure_management", "security_audit", "api_design",
             "code_review", "technical_writing"],
    "CFO": ["financial_modeling", "budget_planning", "forecasting",
             "audit_support", "tax_analysis"],
    "CMO": ["campaign_management", "market_research", "competitive_intelligence",
             "content_writing", "seo_optimization"],
    "COO": ["project_planning", "workflow_optimization", "performance_analysis",
             "procurement_analysis", "meeting_facilitation"],
    "software_engineer": ["code_generation", "code_review", "database_design",
                           "api_design", "technical_writing"],
    "data_scientist": ["data_analysis", "financial_modeling", "forecasting",
                        "report_generation", "knowledge_synthesis"],
    "product_manager": ["product_analysis", "user_research", "decision_analysis",
                         "scenario_planning", "market_research"],
    "marketing_manager": ["campaign_management", "email_marketing", "social_media",
                           "content_writing", "seo_optimization"],
    "sales_representative": ["lead_generation", "email_outreach",
                               "customer_support", "sales_analysis"],
    "legal_counsel": ["legal_review", "contract_analysis", "compliance_check",
                       "risk_assessment"],
    "hr_manager": ["hr_screening", "talent_assessment", "performance_analysis",
                    "report_generation"],
    "security_engineer": ["security_audit", "threat_analysis", "incident_response",
                           "compliance_check"],
    "devops_engineer": ["infrastructure_management", "incident_response",
                         "rpa_automation", "code_review"],
    "content_writer": ["content_writing", "copywriting", "seo_optimization",
                        "translation", "social_media"],
    "financial_analyst": ["financial_modeling", "data_analysis", "forecasting",
                           "budget_planning", "report_generation"],
    "business_analyst": ["data_analysis", "workflow_optimization",
                          "report_generation", "decision_analysis"],
    "researcher": ["web_search", "market_research", "knowledge_synthesis",
                    "competitive_intelligence", "user_research"],
    "customer_support_agent": ["customer_support", "knowledge_search",
                                 "email_outreach", "document_analysis"],
    "procurement_manager": ["procurement_analysis", "vendor_evaluation",
                              "budget_planning", "contract_analysis"],
}


# ─────────────────────────────────────────────────────────────────────────────
#  Department → preferred LLM profile
# ─────────────────────────────────────────────────────────────────────────────
DEPT_LLM_PROFILES: dict[str, str] = {
    "executive": "premium",
    "engineering": "coding",
    "marketing": "creative",
    "sales": "fast",
    "finance": "analytical",
    "legal": "expert",
    "hr": "smart",
    "operations": "smart",
    "research": "research",
    "data": "analytical",
    "security": "expert",
    "support": "fast",
    "content": "creative",
    "product": "analytical",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Gap detection
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GapReport:
    missing: list[str]
    proposed: list[str]
    coverage_pct: float


class CapabilityGapDetector:
    """Detect capabilities required by a mission that are not in the registry."""

    async def detect_gaps(self, required: list[str]) -> GapReport:
        with _tracer.start_as_current_span("capability_registry.detect_gaps") as span:
            span.set_attribute("required_count", len(required))
            missing = [c for c in required if c not in CAPABILITY_REGISTRY]
            proposed = [f"custom_{c}" for c in missing]
            coverage = (len(required) - len(missing)) / max(len(required), 1)
            span.set_attribute("missing_count", len(missing))
            span.set_attribute("coverage_pct", coverage)
            _log.info(
                "capability_gap_detection.done",
                required=len(required),
                missing=len(missing),
                coverage_pct=round(coverage, 3),
            )
            return GapReport(missing=missing, proposed=proposed, coverage_pct=coverage)


# ─────────────────────────────────────────────────────────────────────────────
#  Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_capabilities_for_role(role_name: str) -> list[str]:
    """Return the capability list for a known role name, or [] if unknown."""
    return ROLE_CAPABILITY_MAP.get(role_name, [])


def get_model_profile_for_dept(dept_kind: str) -> str:
    """Return the preferred model profile name for a department kind."""
    return DEPT_LLM_PROFILES.get(dept_kind, "smart")
